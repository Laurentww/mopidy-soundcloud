import os.path
import unittest
from unittest import mock

import vcr

import mopidy_soundcloud
from mopidy.models import Track
from mopidy_soundcloud import Extension
from mopidy_soundcloud.soundcloud import SoundCloudClient, readable_url

local_path = os.path.abspath(os.path.dirname(__file__))
my_vcr = vcr.VCR(
    serializer="yaml",
    cassette_library_dir=local_path + "/fixtures",
    record_mode="once",
    match_on=["uri", "method"],
    decode_compressed_response=False,
    filter_headers=["Authorization"],
)


class ApiTest(unittest.TestCase):
    @my_vcr.use_cassette("sc-login.yaml")
    def setUp(self):
        config = Extension().get_config_schema()
        config["auth_token"] = "3-35204-970067440-lVY4FovkEcKrEGw"
        config["explore_songs"] = 10
        self.api = SoundCloudClient({"soundcloud": config, "proxy": {}})

    def test_sets_user_agent(self):
        agent = "Mopidy-SoundCloud/%s Mopidy/" % mopidy_soundcloud.__version__
        assert agent in self.api.OAuth.headers["user-agent"]

    def test_public_client_no_token(self):
        token_key = "authorization"
        assert token_key not in self.api.session_public.headers._store

    def test_resolves_string(self):
        _id = self.api.parse_track_uri("soundcloud:song.38720262")
        assert _id == "38720262"

    @my_vcr.use_cassette("sc-login-error.yaml")
    def test_responds_with_error(self):
        with mock.patch("mopidy_soundcloud.soundcloud.logger.error") as d:
            config = Extension().get_config_schema()
            config["auth_token"] = "1-fake-token"
            SoundCloudClient({"soundcloud": config, "proxy": {}}).user
            d.assert_called_once_with(
                'Invalid "auth_token" used for SoundCloud authentication!'
            )

    @my_vcr.use_cassette("sc-login.yaml")
    def test_returns_username(self):
        user = self.api.user.get("username")
        assert user == "Nick Steel 3"

    @my_vcr.use_cassette("sc-resolve-track.yaml")
    def test_resolves_object(self):
        trackc = {"uri": "soundcloud:song.38720262"}
        track = Track(**trackc)

        id = self.api.parse_track_uri(track)
        assert id == "38720262"

    @my_vcr.use_cassette("sc-resolve-track-none.yaml")
    def test_resolves_unknown_track_to_none(self):
        track = self.api.get_parsed_track("s38720262")
        assert track is None

    @my_vcr.use_cassette("sc-resolve-track.yaml")
    def test_resolves_track(self):
        track = self.api.get_parsed_track("13158665")
        assert isinstance(track, Track)
        assert track.uri == "soundcloud:song/Munching at Tiannas house.13158665"

    @my_vcr.use_cassette("sc-resolve-http.yaml")
    def test_resolves_http_url(self):
        track = self.api.resolve_url(
            "https://soundcloud.com/bbc-radio-4/m-w-cloud"
        )[0]
        assert isinstance(track, Track)
        assert (
            track.uri
            == "soundcloud:song/That Mitchell and Webb Sound The Cloud.122889665"
        )

    @my_vcr.use_cassette("sc-resolve-set.yaml")
    def test_resolves_set_url(self):
        expected_tracks = [
            "01 Dash And Blast",
            "02 We Flood Empty Lakes",
            "03 A Song For Starlit Beaches",
            "04 Illuminate My Heart, My Darling",
        ]
        tracks = self.api.resolve_url(
            "https://soundcloud.com/yndihalda/sets/dash-and-blast"
        )
        assert len(tracks) == 4
        for i, _ in enumerate(expected_tracks):
            assert isinstance(tracks[i], Track)
            assert tracks[i].name == expected_tracks[i]
            assert tracks[i].length > 500
            assert len(tracks[i].artists) == 1
            assert list(tracks[i].artists)[0].name == "yndi halda"

    @my_vcr.use_cassette("sc-liked.yaml")
    def test_get_user_likes(self):
        tracks = self.api.get_user_favorites()
        assert len(tracks) == 3
        assert isinstance(tracks[0], Track)
        assert tracks[1].name == "Pelican - Deny The Absolute"

    @my_vcr.use_cassette("sc-stream.yaml")
    def test_get_user_stream(self):
        tracks = self.api.get_user_stream()
        assert len(tracks) == 10
        assert isinstance(tracks[0], Track)
        assert tracks[2].name == "JW Ep 20- Jeremiah Watkins"

    @my_vcr.use_cassette("sc-following.yaml")
    def test_get_followings(self):
        users = self.api.get_user_followings()["collection"]
        parsed_users = [
            (user.get("username"), str(user.get("id"))) for user in users
        ]
        assert len(users) == 10
        assert parsed_users[0] == ("Young Legionnaire", "992503")
        assert parsed_users[1] == ("Tall Ships", "1710483")
        assert parsed_users[8] == ("Pelican Song", "27945548")
        assert parsed_users[9] == ("sleepmakeswaves", "1739693")

    @my_vcr.use_cassette("sc-user-tracks.yaml")
    def test_get_user_tracks(self):
        expected_tracks = [
            "The Wait",
            "The Cliff (Palms Remix)",
            "The Cliff (Justin Broadrick Remix)",
            "The Cliff (Vocal Version)",
            "Pelican - The Creeper",
            "Pelican - Lathe Biosas",
            "Pelican - Ephemeral",
            "Pelican - Deny the Absolute",
            "Pelican - Immutable Dusk",
            "Pelican - Strung Up From The Sky",
        ]

        tracks = self.api.get_user_tracks(27945548)
        for i, _ in enumerate(expected_tracks):
            assert isinstance(tracks[i], Track)
            assert tracks[i].name == expected_tracks[i]
            assert tracks[i].length > 500
            assert len(tracks[i].artists) == 1

    @my_vcr.use_cassette("sc-set.yaml")
    def test_get_set(self):
        tracks = self.api.get_set_tracks("10961826")
        assert len(tracks) == 1
        assert isinstance(tracks[0], dict)

    @my_vcr.use_cassette("sc-set-invalid.yaml")
    def test_get_invalid_set(self):
        tracks = self.api.get_set_tracks("blahblahrubbosh")
        assert tracks == []

    @my_vcr.use_cassette("sc-sets.yaml")
    def test_get_sets(self):
        sets = self.api.get_user_sets()
        assert len(sets) == 2
        set = sets[1]
        assert set.get("title") == "Pelican"
        assert set.get("id") == 10961826
        assert len(set.get("tracks")) == 1

    def test_readeble_url(self):
        assert "Barsuk Records" == readable_url('"@"Barsuk      Records')
        assert "_Barsuk Records" == readable_url("_Barsuk 'Records'")

    @my_vcr.use_cassette("sc-resolve-preview-stream.yaml")
    def test_resolves_preview_track(self):
        track = self.api.get_parsed_track("253513246", True)
        assert isinstance(track, Track)
        assert track.name == "Never Gonna Give You Up"
        assert "preview" in track.album.name.lower()
        assert track.uri == (
            "https://cf-hls-media.sndcdn.com/playlist/0/30/YMlUkuvVbZ"
            "ci.128.mp3/playlist.m3u8?Policy=eyJTdGF0ZW1lbnQiOlt7IlJl"
            "c291cmNlIjoiKjovL2NmLWhscy1tZWRpYS5zbmRjZG4uY29tL3BsYXls"
            "aXN0LzAvMzAvWU1sVWt1dlZiWmNpLjEyOC5tcDMvcGxheWxpc3QubTN1"
            "OCIsIkNvbmRpdGlvbiI6eyJEYXRlTGVzc1RoYW4iOnsiQVdTOkVwb2No"
            "VGltZSI6MTYxODI1NzYwMX19fV19&Signature=Ef4z2hktS4b6Lw9ri"
            "BiYie1lFMItNzrZFeTQDvQ1LyZqJ9RZd5aGx4qJyt2FTPMEXKJjuIqrf"
            "PtCpaEizAOpQYI60nEzGFcv0I9jWHZP1bb6OLvkbTJtasWyjRdlJpa--"
            "ks91EyjZE~86ReczDbrGJnNCv1Yxc2Q1alkVZzLWRhAOGwbOECIbLeS2"
            "FmbfXUmzKpNShsoQrwjYzB1wElXYvLIwOZvZBWv2Z-yovwPrjNv~x7Su"
            "a1qvZBXmVzt1akvq9iUA6USioWo0RMPYejBQ7YoqLx-X19J6G1YtPXNC"
            "xbRfS7Z6W62jtxJEv2kiOBJJXFGfnAl-H8p6Qbmconhow__&Key-Pair"
            "-Id=APKAI6TU7MMXM5DG6EPQ"
        )

    @my_vcr.use_cassette("sc-resolve-track-id.yaml")
    def test_unstreamable_track(self):
        track = self.api.OAuth.get("tracks/13158665")
        track["streamable"] = False
        track = self.api.parse_track(track)
        assert track is None

    @my_vcr.use_cassette("sc-resolve-app-client-id.yaml")
    def test_resolves_app_client_id(self):
        track = self.api.OAuth.get("tracks/13158665")
        track = self.api.parse_track(track, True)
        assert track.uri == (
            "https://cf-media.sndcdn.com/fxguEjG4ax6B.128.mp3?Policy="
            "eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiKjovL2NmLW1lZGlhLnNu"
            "ZGNkbi5jb20vZnhndUVqRzRheDZCLjEyOC5tcDMiLCJDb25kaXRpb24i"
            "OnsiRGF0ZUxlc3NUaGFuIjp7IkFXUzpFcG9jaFRpbWUiOjE2MTc4MjY4"
            "ODJ9fX1dfQ__&Signature=Ja0xV7nQgoGFeFDvWhoTZc-iVZtMKUolJ"
            "cC7vxOZ8tES5HcfrwwXxCmKR-BmPiw1i-WgwbiGo6B2ANutpCpYYaMIe"
            "~FYHnQf~j4rZOXd91k6RDYTLURmCMMQuJv1Gy6gyTJXO95DUam3QLg3p"
            "M~cbQsAlZIqjfuT4C4ulapjUfBxt6iXuE-RHPAM5Ac4WvBPgfU5ZDPU5"
            "MGGn3cXNpoRQzPh8P6fQznnI4gInk7bo79owknREcoyytIeD9baZt8dw"
            "Ym34M~ADVjl0QKW7lOA3R-LPpx5NhyvLIEwPKkTC8OBq2Jilai68ml6n"
            "-GcwKHV80kN~BKMFXL2HnxQ4NwVMg__&Key-Pair-Id=APKAI6TU7MMX"
            "M5DG6EPQ"
        )

    @my_vcr.use_cassette("sc-resolve-track-id-invalid-client-id.yaml")
    def test_resolves_stream_track_invalid_client_id(self):
        self.api.public_client_id = "blahblahrubbosh"
        track = self.api.get_parsed_track("13158665", True)
        assert isinstance(track, Track)
        assert track.uri == (
            "https://cf-hls-media.sndcdn.com/playlist/fxguEjG4ax6B.12"
            "8.mp3/playlist.m3u8?Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291c"
            "mNlIjoiKjovL2NmLWhscy1tZWRpYS5zbmRjZG4uY29tL3BsYXlsaXN0L"
            "2Z4Z3VFakc0YXg2Qi4xMjgubXAzL3BsYXlsaXN0Lm0zdTgiLCJDb25ka"
            "XRpb24iOnsiRGF0ZUxlc3NUaGFuIjp7IkFXUzpFcG9jaFRpbWUiOjE2M"
            "TgyNTc2OTV9fX1dfQ__&Signature=KXPxHbwAUOF8Lyf9e696s6SU0~"
            "OiM8dE~7MneU-15l8rtpLpUJ2AA2ZCagAE4YAcA5lNP1wyBtgklx20vX"
            "cQGb7EOeeW6mtBgJuC2tRNgFOkHv5bVnXE3nvWQ7XnaT8uQbmW4fAFDG"
            "CEnF5UD7Ji9l6Qht3emBF8XrPdkYk2GxjnZbMaNmOcfT6sYCxQZrXRhg"
            "-wYPe3uU7XxTMC9pmsYTKuU51e5pV8Q0swAQSEoBBACcvAVQBjjOtr0c"
            "ogrvI90YRARUtc-ggIhafUW5cRLvAF9SxEz33c7JgKEUyg-YJveDlNF7"
            "IhOlLdfEmVe~T~kDRkIcHZtVoi3eWU9gG0ug__&Key-Pair-Id=APKAI"
            "6TU7MMXM5DG6EPQ"
        )

    @my_vcr.use_cassette("sc-resolve-multiple-track-ids.yaml")
    def test_resolves_multiple_tracks_ids(self):
        track_ids = ["13158665", "253513246", "253022611"]
        tracks = self.api.get_tracks_batch(track_ids)
        for track in tracks:
            track = self.api.parse_track(track, True)
            assert isinstance(track, Track)
            assert track.uri.startswith("https://cf-")

    @my_vcr.use_cassette("sc-search.yaml")
    def test_search(self):
        tracks = self.api.search("the great descent")
        assert len(tracks) == 10
        assert isinstance(tracks[0], Track)
        assert tracks[0].name == "Turn Around (Mix1)"

    @my_vcr.use_cassette("sc-resolve-selections.yaml")
    def test_selections(self):
        playlist_key = "playlist_dict"
        selections = self.api.get_selections(playlist_key)
        assert len(selections) == 9
        selection = selections["soundcloud:selections:charts-top"]
        assert selection.get(playlist_key, False)
        playlist_choice = selections["soundcloud:selections:curated:featured"]
        assert len(playlist_choice.get(playlist_key)) == 7
