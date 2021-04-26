import re
import string
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote_plus


class cache:  # noqa
    def __init__(self, ctl=8, ttl=3600):
        self.cache = {}
        self.ctl = ctl
        self.ttl = ttl
        self._call_count = 1

    def __call__(self, func):
        def _memoized(*args):
            self.func = func
            now = time.time()
            try:
                value, last_update = self.cache[args]
                age = now - last_update
                if self._call_count >= self.ctl or age > self.ttl:
                    self._call_count = 1
                    raise AttributeError

                self._call_count += 1
                return value

            except (KeyError, AttributeError):
                value = self.func(*args)
                self.cache[args] = (value, now)
                return value

            except TypeError:
                return self.func(*args)

        return _memoized


def safe_url(uri):
    return quote_plus(
        unicodedata.normalize("NFKD", uri).encode("ASCII", "ignore")
    )


def readable_url(uri):
    valid_chars = f"-_.() {string.ascii_letters}{string.digits}"
    safe_uri = (
        unicodedata.normalize("NFKD", uri).encode("ascii", "ignore").decode()
    )
    return re.sub(
        r"\s+", " ", "".join(c for c in safe_uri if c in valid_chars)
    ).strip()


def get_user_url(user_id):
    return "me" if not user_id else f"users/{user_id}"


def get_datetime(date):
    if date is None:
        return None

    try:
        return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        try:
            return datetime.strptime(date, "%Y/%m/%d %H:%M:%S +0000")
        except ValueError:
            return None


def parse_fail_reason(reason):
    return "" if reason == "Unknown" else f"({reason})"


def pick_transcoding(
    transcodings: list,
    compr_pref="ogg",
    stream_pref="progressive",
):
    """ Picks a transcoding from transcodings according to
    ``stream_pref`` and ``compression_pref``.

    Notes
    -----
    hls streams:
        - More responsive when seeking to specific time in track.
        - Requires gstreamer1.0-plugins-bad.
            ($ sudo apt install gstreamer1.0-plugins-bad)

    Parameters
    ----------
    transcodings : list
        All transcoding options for a SoundCloud track.
    compr_pref : str, optional
        Compression method preference. Choose from ["ogg", "mpeg"].
    stream_pref : str, optional
        Streaming protocol preference. Choose from ["progressive", "hls"].

    Returns
    -------
    transcoding : dict
    
    """  # noqa

    if len(transcodings) == 1:
        return transcodings[0]

    # Remove transcodings of preview streams if full streams are also available
    preview_flags = [1 if "preview" in i["url"] else 0 for i in transcodings]
    if 1 < sum(preview_flags) != len(preview_flags):
        transcodings = [t for t in transcodings if "preview" not in t["url"]]

    second_choice = None
    for t in transcodings:
        if compr_pref in t["format"]["mime_type"]:
            if t["format"]["protocol"] == stream_pref:
                return t
            else:
                second_choice = t

    if second_choice is not None:
        return second_choice

    return transcodings[0]


def sanitize_list(tracks):
    return [t for t in tracks if t]


def get_image_urls(data):
    image_sources = [
        data.get("artwork_url"),
        data.get("calculated_artwork_url"),
    ]

    # Only include avatar images if no other images found
    if image_sources.count(None) == len(image_sources):
        image_sources = [
            data.get("user", {}).get("avatar_url"),
            data.get("avatar_url"),
        ]

    return sanitize_list(image_sources)
