import logging
import re
import urllib.parse

from mopidy import backend, models
from mopidy.models import SearchResult, Track

from mopidy_soundcloud.images import SoundCloudImageProvider
from mopidy_soundcloud.utils import cache, get_image_urls

logger = logging.getLogger(__name__)


def simplify_search_query(query):
    if isinstance(query, dict):
        r = []
        for v in query.values():
            if isinstance(v, list):
                r.extend(v)
            else:
                r.append(v)
        return " ".join(r)
    if isinstance(query, list):
        return " ".join(query)
    else:
        return query


def common_start(*args):
    def _iter():
        for chars in zip(*args):
            if chars.count(chars[0]) == len(chars):
                yield chars[0]
            else:
                return

    return "".join(_iter())


class SoundCloudLibraryProvider(backend.LibraryProvider):
    base_str = "soundcloud"
    dir_str = "directory"
    following_str = "following"
    liked_str = "liked"
    sets_str = "sets"
    stream_str = "stream"
    explore_str = "explore"
    selections_str = "selections"
    search_str = "search"

    selections_playlist_key = "playlist_dict"

    root_directory = models.Ref.directory(
        uri=f"{base_str}:{dir_str}", name="SoundCloud"
    )
    vfs = {root_directory.uri: {}}

    explore_uri = f"{root_directory.uri}:{explore_str}"
    search_uri = f"{base_str}:{search_str}"

    album_image_cache = {}
    selections_pre_string = None

    def __init__(self, web_client, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_to_vfs(self.new_folder("Following", [self.following_str]))
        self.add_to_vfs(self.new_folder("Liked", [self.liked_str]))
        self.add_to_vfs(self.new_folder("Sets", [self.sets_str]))
        self.add_to_vfs(self.new_folder("Stream", [self.stream_str]))
        self.add_to_vfs(self.new_folder("Explore", [self.explore_str]))

        self.vfs[self.explore_uri] = {}

        self.image_provider = SoundCloudImageProvider(
            web_client,
            self.album_image_cache,
        )

    def convert_dir(self, path):
        return f"{self.base_str}:{self.dir_str}:{urllib.parse.quote('/'.join(path))}"

    def new_folder(self, name, path):
        return models.Ref.directory(
            uri=self.convert_dir(path),
            name=name,
        )

    def new_album(self, uri, data, name):
        self.album_image_cache.update({uri: get_image_urls(data)})
        return models.Ref.album(uri=uri, name=name)

    def get_images(self, uris):
        return self.image_provider.get_images(uris)

    def add_to_vfs(self, _model):
        self.vfs[self.root_directory.uri][_model.uri] = _model

    def get_selections(self):
        selections = self.backend.remote.get_selections(
            self.selections_playlist_key
        )
        if selections and self.selections_pre_string is None:
            keys = tuple(selections.keys())
            if len(keys) == 1:
                self.selections_pre_string = ":".join(keys[0].split(":")[:2])
            else:
                self.selections_pre_string = common_start(*keys).strip(":")

        return selections

    def list_sets(self):
        sets_vfs = []
        for set in self.backend.remote.get_user_sets():
            name = set.get("title")
            uri = self.convert_dir([self.sets_str, str(set.get("id"))])
            sets_vfs.append(self.new_album(uri, set, name))
            logger.debug(f"Adding set {name} to VFS")
        return sets_vfs

    def list_liked(self):
        vfs_list = []
        for track in self.backend.remote.get_user_favorites():
            logger.debug(f"Adding liked track {track.name} to VFS")
            vfs_list.append(models.Ref.track(uri=track.uri, name=track.name))
        return vfs_list

    def list_user_follows(self):
        user_follows = []
        user_follows_imported = self.backend.remote.get_user_followings()
        for followed_user in user_follows_imported.get("collection", []):
            uri = self.convert_dir(
                [self.following_str, str(followed_user.get("id"))]
            )
            name = followed_user.get("username", "")
            user_follows.append(self.new_album(uri, followed_user, name))
            logger.debug(f"Adding followed used {name} to VFS")
        return user_follows

    def list_selections(self, uri):
        selections_vfs = []
        for selection in self.get_selections().values():
            name = selection.get("title")
            selection_id = str(selection.get("id"))
            urn = selection_id.replace(self.selections_pre_string, "")
            urn = urn.strip(":")
            selection = models.Ref.directory(
                uri=f"{uri}:{urn}",
                name=name,
            )
            self.vfs[uri][selection.uri] = selection
            logger.debug(f"Adding selection {selection.name} to VFS")
            selections_vfs.append(selection)
        return selections_vfs

    def convert_url(self, url, into=None):
        if into is None:
            into = self.selections_str
        return url.replace(f"{self.dir_str}:{self.explore_str}", into)

    def list_selection_playlists(self, uri: str):
        # TODO: pagination for more playlists (``next_href`` property)
        selections = self.get_selections()  # from cache
        playlists = selections[self.convert_url(uri)]
        playlists_vfs = []
        for playlist in playlists[self.selections_playlist_key].values():
            if isinstance(playlist["id"], int):
                playlist_uri = f"{uri}:{str(playlist['id'])}"
            else:
                split_str = uri.split(":")[-1]
                playlist_uri = f"{uri}{playlist['id'].split(split_str)[-1]}"

            name = playlist["title"]
            logger.debug(f"Adding selection playlist {name} to VFS")
            playlists_vfs.append(self.new_album(playlist_uri, playlist, name))

        return playlists_vfs

    @cache()
    def list_system_playlist_tracks(self, uri):
        # remove 'soundcloud:directory:explore:' from beginning of uri
        uri_ends = uri.split(self.explore_str)[1:]
        uri_parts = self.explore_str.join(uri_ends).strip(":").split(":")

        if uri_parts[-1].isnumeric():
            select_parts = uri_parts[:-1]
            key_playlist = int(uri_parts[-1])
        else:
            select_parts = [uri_parts[0]]
            key_playlist = self.convert_url(uri, into="system-playlists")

        key_selection = f"{self.selections_pre_string}:{':'.join(select_parts)}"

        selections = self.get_selections()  # from cache
        selection = selections[key_selection][self.selections_playlist_key]
        playlist = selection[key_playlist]
        if playlist.get("tracks"):
            track_ids = [track["id"] for track in playlist["tracks"]]
            # Track jsons do not contain stream urls. Separate API call needed
            return self.backend.remote.get_tracks_batch(track_ids)

        # Only set id known
        return self.backend.remote.get_set_tracks(str(playlist["id"]))

    def tracklist_to_vfs(self, track_list):
        vfs_list = []
        for temp_track in track_list:
            if not isinstance(temp_track, Track):
                temp_track = self.backend.remote.parse_track(temp_track)
            if hasattr(temp_track, "uri"):
                vfs_list.append(
                    models.Ref.track(uri=temp_track.uri, name=temp_track.name)
                )
        return vfs_list

    def browse(self, uri):
        if not self.vfs.get(uri):
            (req_type, res_id) = re.match(r".*:(\w*)(?:/(\d*))?", uri).groups()
            # Sets
            if self.sets_str == req_type:
                if res_id:
                    return self.tracklist_to_vfs(
                        self.backend.remote.get_set_tracks(res_id)
                    )
                else:
                    return self.list_sets()
            # Following
            if self.following_str == req_type:
                if res_id:
                    return self.tracklist_to_vfs(
                        self.backend.remote.get_user_tracks(res_id)
                    )
                else:
                    return self.list_user_follows()
            # Liked
            if self.liked_str == req_type:
                return self.list_liked()
            # User stream
            if self.stream_str == req_type:
                return self.tracklist_to_vfs(
                    self.backend.remote.get_user_stream()
                )
            # Explore
            if self.explore_str == req_type:
                return self.list_selections(uri)
            elif self.explore_str in uri:
                if uri in self.vfs.get(self.explore_uri, {}):
                    # Explore selections folders
                    return self.list_selection_playlists(uri)
                else:
                    return self.tracklist_to_vfs(
                        self.list_system_playlist_tracks(uri)
                    )

        # root directory
        return list(self.vfs.get(uri, {}).values())

    def search(self, query=None, uris=None, exact=False):
        # TODO Support exact search

        if not query:
            return

        if "uri" in query:
            search_query = "".join(query["uri"])
            url = urllib.parse.urlparse(search_query)
            if "soundcloud.com" in url.netloc:
                logger.info(f"Resolving SoundCloud for: {search_query}")
                return SearchResult(
                    uri=self.search_uri,
                    tracks=self.backend.remote.resolve_url(search_query),
                )
        else:
            search_query = simplify_search_query(query)
            logger.info(f"Searching SoundCloud for: {search_query}")
            return SearchResult(
                uri=self.search_uri,
                tracks=self.backend.remote.search(search_query),
            )

    def lookup(self, uri):
        if "sc:" in uri:
            uri = uri.replace("sc:", "")
            return self.backend.remote.resolve_url(uri)

        if uri.startswith(self.root_directory.uri):
            return []

        try:
            track_id = self.backend.remote.parse_track_uri(uri)
            track = self.backend.remote.get_parsed_track(track_id)
            if track is None:
                logger.info(
                    f"Failed to lookup {uri}: SoundCloud track not found"
                )
                return []
            return [track]
        except Exception as error:
            logger.error(f"Failed to lookup {uri}: {error}")
            return []
