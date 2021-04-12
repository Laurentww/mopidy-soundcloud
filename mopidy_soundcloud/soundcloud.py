import collections
import logging
import re
from datetime import datetime, timedelta
from contextlib import closing
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError

import mopidy_soundcloud
from mopidy import httpclient
from mopidy.models import Album, Artist, Track
from mopidy_soundcloud.utils import (
    cache,
    readable_url,
    get_user_url,
    get_datetime,
    pick_transcoding,
    parse_fail_reason,
    sanitize_list,
)

logger = logging.getLogger(__name__)

# import vcr
# import os
# my_vcr = vcr.VCR(
#     serializer="yaml",
#     cassette_library_dir=os.path.abspath(os.path.dirname(__file__)) + "/fixtures",
#     record_mode="once",
#     match_on=["uri", "method"],
#     decode_compressed_response=False,
# )


class ThrottlingHttpAdapter(HTTPAdapter):
    def __init__(self, burst_length, burst_window, wait_window):
        super().__init__()
        self.max_hits = burst_length
        self.hits = 0
        self.rate = burst_length / burst_window
        self.burst_window = timedelta(seconds=burst_window)
        self.total_window = timedelta(seconds=burst_window + wait_window)
        self.timestamp = datetime.min

    def _is_too_many_requests(self):
        now = datetime.utcnow()
        if now < self.timestamp + self.total_window:
            elapsed = now - self.timestamp
            self.hits += 1
            if (now < self.timestamp + self.burst_window) and (
                self.hits < self.max_hits
            ):
                return False
            else:
                logger.debug(
                    f"Request throttling after {self.hits} hits in "
                    f"{elapsed.microseconds} us "
                    f"(window until {self.timestamp + self.total_window})"
                )
                return True
        else:
            self.timestamp = now
            self.hits = 0
            return False

    def send(self, request, **kwargs):
        if request.method == "HEAD" and self._is_too_many_requests():
            resp = requests.Response()
            resp.request = request
            resp.url = request.url
            resp.status_code = 429
            resp.reason = (
                "Client throttled to {self.rate:.1f} requests per second"
            )
            return resp
        else:
            return super().send(request, **kwargs)


class SoundCloudSession(requests.Session):
    OAuth = False
    client_param = None

    def __init__(self, config, host, explore_songs, client_id=None):
        super().__init__()
        self.api_host = host
        self.client_id = client_id
        self.explore_songs = explore_songs

        proxy = httpclient.format_proxy(config["proxy"])
        self.proxies.update({"http": proxy, "https": proxy})

        if self.client_id:
            full_user_agent = httpclient.format_user_agent(
                f"{mopidy_soundcloud.Extension.dist_name}/"
                f"{mopidy_soundcloud.__version__}"
            )
            add_headers = {
                "user-agent": full_user_agent,
                "Authorization": f"OAuth {config['soundcloud']['auth_token']}",
            }
            self.headers.update(add_headers)
            self.OAuth = True

        adapter = ThrottlingHttpAdapter(
            burst_length=3, burst_window=1, wait_window=10
        )
        self.mount(self.api_host, adapter)

    @property
    def client_id(self):
        return self._client_id

    @client_id.setter
    def client_id(self, client_id):
        self.client_param = ("client_id", client_id)
        self._client_id = client_id

    def get_request(self, *args, **kwargs):
        return super().get(*args, **kwargs)

    def get(self, filename, limit=None) -> dict:
        if not self.OAuth and self.client_id is None:
            self.update_public_client_id()

        params = [self.client_param]
        if limit:
            limit_int = limit if type(limit) == int else self.explore_songs
            params = [("limit", limit_int)] + params

        url = f"{self.api_host}/{filename}"
        try:
            with closing(self.get_request(url, params=params)) as res:
                logger.debug(f"Requested {res.url}")
                res.raise_for_status()
                return res.json()
        except Exception as e:
            if isinstance(e, HTTPError) and e.response.status_code == 401:
                if self.OAuth:
                    logger.error(
                        'Invalid "auth_token" used for SoundCloud '
                        "authentication!"
                    )
                else:
                    logger.error(f"SoundCloud API request failed: {e}")
            else:
                logger.error(f"SoundCloud API request failed: {e}")
        return {}

    def get_public_stream(self, transcoding):
        return self.get_request(transcoding["url"], params=[self.client_param])

    def update_public_client_id(self):
        """ Gets a client id which can be used to stream publicly available tracks """

        def _get_page(url):
            return self.get_request(url).content.decode("utf-8")

        public_page = _get_page("https://soundcloud.com/")
        regex_str = r"client_id=([a-zA-Z0-9]{16,})"
        soundcloud_soup = BeautifulSoup(public_page, "html.parser")
        scripts = soundcloud_soup.find_all("script", attrs={"src": True})
        for script in scripts:
            for match in re.finditer(regex_str, _get_page(script["src"])):
                self.client_id = match.group(1)
                logger.debug(
                    f"Updated SoundCloud public client id to: {self.client_id}"
                )
                return


class SoundCloudClient:
    CLIENT_ID = "93e33e327fd8a9b77becd179652272e2"

    auth_error_codes = [401, 403, 429]
    api_url = {
        "v1": "https://api.soundcloud.com",
        "v2": "https://api-v2.soundcloud.com",
    }

    def __init__(self, config):
        super().__init__()
        explore_songs = config["soundcloud"].get("explore_songs", 25)
        self.streaming_pref = config["soundcloud"].get("stream_pref")
        if self.streaming_pref is None:
            self.streaming_pref = "progressive"

        self.OAuth = SoundCloudSession(
            config,
            self.api_url["v1"],
            explore_songs,
            client_id=self.CLIENT_ID,
        )
        self.session_public = SoundCloudSession(
            config,
            self.api_url["v2"],
            explore_songs,
        )

    @property
    @cache()
    def user(self):
        return self.OAuth.get("me")

    @cache(ttl=10)
    def get_user_stream(self):
        # https://developers.soundcloud.com/docs/api/reference#activities
        tracks = []
        stream = self.OAuth.get("me/activities", limit=True).get(
            "collection", []
        )
        for data in stream:
            kind = data.get("origin")
            # multiple types of track with same data
            if kind:
                if kind["kind"] == "track":
                    tracks.append(self.parse_track(kind))
                elif kind["kind"] == "playlist":
                    playlist = kind.get("tracks")
                    if isinstance(playlist, collections.Iterable):
                        tracks.extend(self.parse_results(playlist))

        return sanitize_list(tracks)

    @cache(ttl=10)
    def get_user_followings(self, user_id=None):
        user_url = get_user_url(user_id)
        playlists = self.OAuth.get(f"{user_url}/followings", limit=True)
        for playlist in playlists.get("collection", []):
            user_name = playlist.get("username")
            user_id = str(playlist.get("id"))
            logger.debug(f"Fetched user {user_name} with ID {user_id}")
        return playlists

    @cache()
    def get_set(self, set_id):
        # https://developers.soundcloud.com/docs/api/reference#playlists
        # Returns full results only using standard app client_id and API-v1
        return self.OAuth.get(f"playlists/{set_id}")

    def get_set_tracks(self, set_id):
        playlist = self.get_set(set_id)
        return playlist.get("tracks", [])

    @cache(ttl=10)
    def get_user_sets(self, user_id=None):
        user_url = get_user_url(user_id)
        playable_sets = self.OAuth.get(f"{user_url}/playlists", limit=True)
        for playlist in playable_sets:
            name = playlist.get("title")
            set_id = str(playlist.get("id"))
            tracks = playlist.get("tracks", [])
            logger.debug(
                f"Fetched set {name} with ID {set_id} ({len(tracks)} tracks)"
            )
        return playable_sets

    @cache(ttl=10)
    def get_user_favorites(self, user_id=None):
        # https://developers.soundcloud.com/docs/api/reference#GET--users--id--favorites
        user_url = get_user_url(user_id)
        likes = self.OAuth.get(f"{user_url}/favorites", limit=True)
        return self.parse_results(likes)

    @cache(ttl=10)
    def get_user_tracks(self, user_id=None):
        user_url = get_user_url(user_id)
        # Only works using standard app client_id and API-v1
        tracks = self.OAuth.get(f"{user_url}/tracks", limit=True)
        return self.parse_results(tracks)

    # @my_vcr.use_cassette("sc-resolve-selections.yaml")
    @cache(ttl=600)
    def get_selections(self, playlist_key, limit=10):
        selections = {}
        explored_selections = self.session_public.get(
            "mixed-selections",
            limit=limit,
        )
        for selection in explored_selections["collection"]:
            selections[selection["id"]] = selection
            if not selection.get("items"):
                continue

            # Save playlists into dict for ease of use
            playlists = selection.get("items").get("collection", [])
            selection[playlist_key] = {}
            for playlist in playlists:
                selection[playlist_key][playlist["id"]] = playlist

            if playlist_key == "items":
                for key in ["next_href", "query_urn", "collection"]:
                    selection["items"].pop(key)
            else:
                selection.pop("items")

            logger.debug(
                f"Fetched selection {selection.get('title')} with ID "
                f"{selection['id']} ({len(selection[playlist_key])} playlists)"
            )
        return selections

    # Public
    @cache()
    def get_parsed_track(self, track_id, streamable=False):
        try:
            track = self.get_track(track_id)
            return self.parse_track(track, streamable)
        except Exception:
            return None

    def get_track(self, track_id):
        logger.debug(f"Getting info for track with ID {track_id}")

        url = f"tracks/{track_id}"

        # Try with public client first (API v2)
        res = self.session_public.get(url)
        if res.get("media"):
            return res

        logger.debug(
            f"Failed public (API-v2) call with url: {url}.\n"
            f"Trying OAuth (API-v1) call with url: {url} ..."
        )
        # Try using standard app client_id (API v1)
        return self.OAuth.get(url)

    def get_tracks_batch(self, track_ids):
        tracks_str = ",".join([str(track) for track in track_ids])
        logger.debug(f"Getting info for tracks with IDs {tracks_str}")

        url = f"tracks?ids={tracks_str}"
        res_batch = self.session_public.get(url)

        # Try one at a time if batch request failed
        if not res_batch:
            res_batch = []
            for track_id in track_ids:
                res_batch.append(self.get_track(track_id))
        return res_batch

    @staticmethod
    def parse_track_uri(track):
        logger.debug(f"Parsing track {track}")
        if hasattr(track, "uri"):
            track = track.uri
        return track.split(".")[-1]

    def search(self, query):
        # https://developers.soundcloud.com/docs/api/reference#tracks
        # Still only works using standard app client_id and API-v1
        query = quote_plus(query.encode("utf-8"))
        search_results = self.OAuth.get(f"tracks?q={query}", limit=True)
        tracks = []
        for track in search_results:
            tracks.append(self.parse_track(track, False))
        return sanitize_list(tracks)

    def parse_results(self, res):
        tracks = []
        logger.debug(f"Parsing {len(res)} result item(s)...")
        for item in res:
            if item["kind"] == "track":
                tracks.append(self.parse_track(item))
            elif item["kind"] == "playlist":
                playlist_tracks = item.get("tracks", [])
                logger.debug(
                    f"Parsing {len(playlist_tracks)} playlist track(s)..."
                )
                for track in playlist_tracks:
                    tracks.append(self.parse_track(track))
            else:
                logger.warning(f"Unknown item type {item['kind']!r}")
        return sanitize_list(tracks)

    def resolve_url(self, uri):
        res = self.OAuth.get(f"resolve?url={uri}")
        return self.parse_results([res])

    @cache()
    def parse_track(self, data, remote_url=False):
        if not data:
            return None
        if not data.get("streamable"):
            logger.info(
                f"{data.get('title')!r} can't be streamed from SoundCloud"
            )
            return None
        if not data.get("kind") == "track":
            logger.debug(f"{data.get('title')} is not a track")
            return None

        track_kwargs = {}
        artist_kwargs = {}
        album_kwargs = {}

        if "title" in data:
            label_name = data.get("label_name")
            if not label_name:
                label_name = data.get("user", {}).get(
                    "username", "Unknown label"
                )

            track_kwargs["name"] = data["title"]
            artist_kwargs["name"] = label_name
            album_kwargs["name"] = "SoundCloud"

        # Maybe identify stream as preview in track description
        if data.get("policy") and data["policy"] == "SNIP":
            addition = " - Preview (Get SoundCloud GO for full stream)"
            album_kwargs["name"] += addition

        track_kwargs["date"] = get_datetime(data.get("created_at")).strftime(
            "%Y-%m-%d"
        )
        if data.get("last_modified"):
            seconds = get_datetime(data["last_modified"]).timestamp()
            track_kwargs["last_modified"] = int(seconds * 1000)

        track_kwargs["genre"] = data.get("genre")

        if remote_url:
            track_kwargs["uri"] = self.get_streamable_url(data)
            if track_kwargs["uri"] is None:
                logger.info(
                    f"{data.get('title')} can't be streamed from SoundCloud"
                )
                return None
        else:
            track_kwargs[
                "uri"
            ] = f"soundcloud:song/{readable_url(data.get('title'))}.{data.get('id')}"

        track_kwargs["length"] = int(data.get("duration", 0))
        track_kwargs["comment"] = data.get("permalink_url", "")
        description = data.get("description")
        if description:
            track_kwargs["comment"] += " - " + description

        if artist_kwargs:
            track_kwargs["artists"] = [Artist(**artist_kwargs)]

        if album_kwargs:
            track_kwargs["album"] = Album(**album_kwargs)

        return Track(**track_kwargs)

    def get_streamable_url(self, track):
        transcoding = {}
        if track.get("media", {}).get("transcodings"):
            # Track obtained through API-v2 has "media" field
            transcoding = pick_transcoding(
                track["media"]["transcodings"],
                stream_pref=self.streaming_pref,
            )

        if transcoding:
            stream = self.session_public.get_public_stream(transcoding)
            if stream.status_code in self.auth_error_codes:
                self.session_public.update_public_client_id()  # refresh once
                stream = self.session_public.get_public_stream(transcoding)

            try:
                return stream.json().get("url")
            except Exception as e:
                logger.info(
                    "Streaming of public song using public client id failed, "
                    "trying with standard application client id.."
                )
                logger.debug(
                    f"Caught public client id stream failure:\n{e}"
                    f"\n{parse_fail_reason(stream.reason)}"
                )

        # Get stream using standard app client_id. (Quickly yields rate limit errors)
        if not track.get("stream_url"):
            track = self.OAuth.get(f"tracks/{track['id']}")
        req_url = f"{track['stream_url']}?client_id={self.CLIENT_ID}"
        req = self.OAuth.head(req_url)
        if req.status_code == 302:
            return req.headers.get("Location", None)
        elif req.status_code == 429:
            logger.warning(
                "SoundCloud daily rate limit exceeded on application client id"
                f"{parse_fail_reason(req.reason)}"
            )

    # def parse_parallel(self, tracks, streamable):
    #     from multiprocessing.pool import ThreadPool
    #     from itertools import repeat
    #     pool = ThreadPool(processes=2)
    #     tracks = pool.starmap(self.parse_track, zip(tracks, repeat(streamable)))
    #     pool.close()
    #     return self.sanitize_tracks(tracks)
