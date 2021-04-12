import itertools
import logging
import operator
import urllib.parse

from mopidy import models
from mopidy_soundcloud.soundcloud import SoundCloudClient
from mopidy_soundcloud.utils import cache, get_image_urls

# NOTE: current file adapted from https://github.com/mopidy/mopidy-spotify
#   - /mopidy-spotify/images.py

logger = logging.getLogger(__name__)


class SoundCloudImageProvider:
    _API_MAX_IDS_PER_REQUEST = 50

    # TODO: possibly fallback to smaller images on fail
    _ARTWORK_MAP = {
        "mini": 16,
        "tiny": 20,
        "small": 32,
        "badge": 47,
        "t67x67": 67,
        "large": 100,
        "t300x300": 300,
        "crop": 400,
        "t500x500": 500,
        "original": 0,
    }

    album_uri = {}

    def __init__(self, web_client: SoundCloudClient, album_art_cache=None):
        self.web_client = web_client

        if album_art_cache is not None:
            self.album_uri = album_art_cache

    def get_images(self, uris):
        result = {}
        uri_type_getter = operator.itemgetter("type")
        uris = sorted((self._parse_uri(u) for u in uris), key=uri_type_getter)
        for uri_type, group in itertools.groupby(uris, uri_type_getter):
            batch = []
            for uri in group:
                if uri_type == "playlist":
                    result.update(self._process_playlist(uri))
                elif uri_type == "selection":
                    result.update(self._process_selection(uri))
                else:
                    batch.append(uri)
                    if len(batch) >= self._API_MAX_IDS_PER_REQUEST:
                        result.update(self._process_uris(batch))
                        batch = []
            result.update(self._process_uris(batch))
        return result

    @cache()
    def _parse_uri(self, uri):
        parsed_uri = urllib.parse.urlparse(uri)
        uri_type, uri_id = None, None

        if parsed_uri.scheme == "soundcloud":
            if uri in self.album_uri:
                uri_type = "selection"
                uri_id = parsed_uri.path.split(":")[-1]
            else:
                uri_type, uri_id = parsed_uri.path.split("/")[:2]
        elif parsed_uri.scheme in ("http", "https"):
            if "soundcloud.com" in parsed_uri.netloc:
                uri_type, uri_id = parsed_uri.path.split("/")[1:3]

        supported_types = ("song", "album", "artist", "playlist", "selection")
        if uri_type and uri_type in supported_types and uri_id:
            return {
                "uri": uri,
                "type": uri_type,
                "id": self.web_client.parse_track_uri(uri_id),
            }

        raise ValueError(f"Could not parse {repr(uri)} as a SoundCloud URI")

    @cache()
    def _process_playlist(self, uri):
        set_images = tuple()
        for track in self.web_client.get_set_tracks(uri["id"]):
            track_images = self._parse_for_images(track)
            set_images += (*track_images,)

        return {uri["uri"]: set_images}

    @cache()
    def _process_selection(self, uri):
        images = []
        for image_url in self.album_uri.get(uri["uri"], []):
            images.append(self._parse_image_url(image_url))
        return {uri["uri"]: tuple(images)}

    def _process_uris(self, uris):
        if not uris:
            return {}

        return {uri["uri"]: self._process_track(uri) for uri in uris}

    @cache()
    def _process_track(self, uri):
        track = self.web_client.get_track(uri["id"])
        return self._parse_for_images(track)

    def _parse_for_images(self, data):
        images = []
        for image_url in get_image_urls(data):
            images.append(self._parse_image_url(image_url))
        return tuple(images)

    def _parse_image_url(self, image_url, im_size="t500x500"):
        image_url = image_url.replace("large", im_size)
        return models.Image(
            uri=image_url,
            height=self._ARTWORK_MAP[im_size],
            width=self._ARTWORK_MAP[im_size],
        )
