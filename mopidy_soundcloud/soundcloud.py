import collections
import logging
from typing import Union
from urllib.parse import quote_plus

from mopidy.models import Album, Artist, Track

from mopidy_soundcloud.web import SoundCloudSession
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


class SoundCloudClient:
    CLIENT_ID = "93e33e327fd8a9b77becd179652272e2"

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
        if explored_selections.get("collection"):
            for selection in explored_selections["collection"]:
                selections[selection["id"]] = selection
                selection[playlist_key] = {}

                if not selection.get("items"):
                    continue

                # Save playlists into dict for ease of use
                playlists = selection.get("items").get("collection", [])
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

    def parse_results(
        self,
        res: Union[collections.Sized, collections.Iterable],
    ):
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
            stream = self.session_public.get_stream(transcoding)
            try:
                return stream.json.get("url")
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
