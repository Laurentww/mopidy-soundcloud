import unittest

import pykka

from mopidy.models import Ref, Track
from mopidy_soundcloud import Extension, actor
from mopidy_soundcloud.library import (
    SoundCloudLibraryProvider,
    simplify_search_query,
)
from mopidy_soundcloud.utils import safe_url
from tests.test_api import my_vcr


class ApiTest(unittest.TestCase):
    def setUp(self):
        config = Extension().get_config_schema()
        config["auth_token"] = "3-35204-970067440-lVY4FovkEcKrEGw"
        config["explore_songs"] = 25
        # using this user http://maildrop.cc/inbox/mopidytestuser
        config = {"soundcloud": config, "proxy": {}}
        self.soundCloudBackend = actor.SoundCloudBackend(config, audio=None)

        self.backend = self.soundCloudBackend.start(
            config=config, audio=None
        ).proxy()
        self.library = SoundCloudLibraryProvider(
            self.soundCloudBackend.remote, backend=self.soundCloudBackend
        )

    def tearDown(self):
        pykka.ActorRegistry.stop_all()

    def test_add_folder(self):
        assert self.library.new_folder("Test", ["test"]) == Ref(
            name="Test", type="directory", uri="soundcloud:directory:test"
        )

    def test_mpc_search(self):
        assert (
            simplify_search_query({"any": ["explosions in the sky"]})
            == "explosions in the sky"
        )

    def test_moped_search(self):
        assert (
            simplify_search_query(
                {
                    "track_name": ["explosions in the sky"],
                    "any": ["explosions in the sky"],
                }
            )
            == "explosions in the sky explosions in the sky"
        )

    def test_simple_search(self):
        assert (
            simplify_search_query("explosions in the sky")
            == "explosions in the sky"
        )

    def test_aria_search(self):
        assert (
            simplify_search_query(["explosions", "in the sky"])
            == "explosions in the sky"
        )

    def test_only_resolves_soundcloud_uris(self):
        assert (
            self.library.search(
                {"uri": "http://www.youtube.com/watch?v=wD6H6Yhluo8"}
            )
            is None
        )

    def test_returns_url_safe_string(self):
        assert (
            safe_url("Alternative/Indie/rock/pop ")
            == "Alternative%2FIndie%2Frock%2Fpop+"
        )
        assert (
            safe_url("D∃∃P Hau⑀ iNDiE DᴬNCE | №➊ ²⁰¹⁴")
            == "DP+Hau+iNDiE+DANCE+%7C+No+2014"
        )

    def test_default_folders(self):
        assert self.library.browse("soundcloud:directory") == [
            Ref(
                name="Following",
                type="directory",
                uri="soundcloud:directory:following",
            ),
            Ref(
                name="Liked", type="directory", uri="soundcloud:directory:liked"
            ),
            Ref(name="Sets", type="directory", uri="soundcloud:directory:sets"),
            Ref(
                name="Stream",
                type="directory",
                uri="soundcloud:directory:stream",
            ),
            Ref(
                name="Explore",
                type="directory",
                uri="soundcloud:directory:explore",
            ),
        ]

    def test_default_folders_lookup(self):
        assert self.library.lookup("soundcloud:directory") == []
        subfolders = ["stream", "liked", "sets", "following"]
        for subfolder in subfolders:
            subfolder = "soundcloud:directory:" + subfolder
            assert self.library.lookup(subfolder) == []
        assert self.library.lookup("soundcloud:non-existent") == []

    @my_vcr.use_cassette("sc-images-track.yaml")
    def test_track_images(self):
        uri_str = "soundcloud:song/Munching at Tiannas house.13158665"
        image_uri = (
            "https://i1.sndcdn.com/avatars-000004193858-jnf2pd-t500x500.jpg"
        )
        images = self.library.image_provider.get_images([uri_str])
        assert len(images[uri_str]) == 1
        check_uri = images[uri_str][0]._uri
        assert check_uri == image_uri

    @my_vcr.use_cassette("sc-images-playlist.yaml")
    def test_playlist_images(self):
        uri_str = "soundcloud:playlist/Old Songs Throwback.1129540288"
        image_uri = "https://i1.sndcdn.com/artworks-aaArnHd1VBTE-0-t500x500.jpg"
        images = self.library.image_provider.get_images([uri_str])
        assert len(images[uri_str]) == 64
        check_uri = images[uri_str][0]._uri
        assert check_uri == image_uri

    @my_vcr.use_cassette("sc-resolve-selections-library.yaml")
    def test_explore(self):
        selection_vfs = self.library.browse("soundcloud:directory:explore")
        assert len(selection_vfs) == 9

        selection_1 = self.library.browse(selection_vfs[0].uri)
        assert len(selection_1) == 42
        selection_2 = self.library.browse(selection_vfs[3].uri)
        assert len(selection_2) == 50

        playlist_1 = self.library.browse(selection_1[3].uri)
        assert playlist_1[42].name == "Official Kruzo Mixtape #9 (Yearmix 2020)"
        playlist_2 = self.library.browse(selection_2[4].uri)
        assert len(playlist_2) == 33
        assert playlist_2[2].name == "Faithless - Insomnia (Calippo Remix)"

    @my_vcr.use_cassette("sc-resolve-search.yaml")
    def test_search(self):
        result = self.library.search(
            query={"artist": ["Munching at Tiannas house"]},
            uris=["soundcloud:"],
            exact=False,
        )
        first_track = result.tracks[0]
        assert isinstance(first_track, Track)
        track_comment_end = "alex-stevenson/munching-at-tiannas-house"
        assert first_track.comment.endswith(track_comment_end)
