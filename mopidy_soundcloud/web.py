import http.client
import json
import logging
import re
import urllib.parse
from contextlib import closing
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from urllib.request import (
    Request,
    ProxyHandler,
    HTTPCookieProcessor,
    OpenerDirector,
    DataHandler,
    UnknownHandler,
    HTTPHandler,
    HTTPSHandler,
    HTTPDefaultErrorHandler,
    HTTPRedirectHandler,
    FTPHandler,
    FileHandler,
    HTTPErrorProcessor,
)

from bs4 import BeautifulSoup
from mopidy import httpclient
from requests import Response, Session
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError

import mopidy_soundcloud

logger = logging.getLogger(__name__)

auth_error_codes = [401, 403, 429]


class SoundCloudSession:
    """ SoundCloud HTTP Session Client """

    throttling = {
        "burst_length": 3,
        "burst_window": 1,
        "wait_window": 10,
    }
    encoding = "utf-8"

    OAuth = False
    client_param = None
    headers = {}
    proxies = {}

    def __init__(self, config, host, explore_songs, client_id=None):

        self.api_host = host
        self.client_id = client_id
        self.explore_songs = explore_songs

        self.proxy = httpclient.format_proxy(config["proxy"])

        if self.client_id:
            full_user_agent = httpclient.format_user_agent(
                f"{mopidy_soundcloud.Extension.dist_name}/"
                f"{mopidy_soundcloud.__version__}"
            )
            self.headers = {
                "user-agent": full_user_agent,
                "Authorization": f"OAuth {config['soundcloud']['auth_token']}",
            }
            self.OAuth = True

        self.session = RequestsSession(
            self.api_host,
            self.headers,
            self.throttling,
            self.proxy,
            self.encoding,
        )

    @property
    def client_id(self):
        return self._client_id

    @client_id.setter
    def client_id(self, client_id):
        self.client_param = [("client_id", client_id)]
        self._client_id = client_id

    def get(self, filename, limit=None) -> dict:
        if not self.OAuth and self._client_id is None:
            self.update_public_client_id()

        params = self.client_param
        if limit:
            limit_int = limit if type(limit) == int else self.explore_songs
            params = [("limit", limit_int)] + params

        url = f"{self.api_host}/{filename}"
        try:
            with closing(self.session.get_request(url, params=params)) as res:
                raise_for_status(res)
                logger.debug(f"Requested {res.url}")
                return res.json
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

    def _get_stream(self, transcoding):
        return self.session.get_request(
            transcoding["url"], params=self.client_param
        )

    def get_stream(self, transcoding):
        stream = self._get_stream(transcoding)
        if stream.status_code in auth_error_codes:
            self.update_public_client_id()  # refresh once
            stream = self._get_stream(transcoding)
        return stream

    def update_public_client_id(
        self, main_url="https://soundcloud.com/", timeout=5
    ):
        """ Gets a client id which is used to stream publicly available tracks """

        try:
            # When SoundCloud has internal server error, timeout occurs.
            # Using an Urllib session resolves the issue
            public_page = self.session.get_page_data(main_url, timeout=timeout)

        except TimeoutError as e:
            if isinstance(self.session, RequestsSession):
                self.session = UrllibSession(
                    self.headers, self.throttling, self.proxy, self.encoding
                )
                logger.debug(
                    "Reverted to Urllib session due to timeout in Requests session"
                )
                public_page = self.session.get_page_data(main_url)
            else:
                raise e

        regex_str = r"client_id=([a-zA-Z0-9]{16,})"
        soundcloud_soup = BeautifulSoup(public_page, "html.parser")
        scripts = soundcloud_soup.find_all("script", attrs={"src": True})
        for script in scripts:
            matches = re.finditer(
                regex_str,
                self.session.get_page_data(script["src"]),
            )
            for match in matches:
                self.client_id = match.group(1)
                logger.debug(
                    f"Updated SoundCloud public client id to: {self.client_id}"
                )
                return

        logger.warning("Failed to obtain public client id")

    def head(self, *args, **kwargs):
        return self.session.head(*args, **kwargs)


class HTTPThrottler(HTTPAdapter, OpenerDirector):
    def __init__(
        self,
        parent_type,
        burst_length=3,
        burst_window=1,
        wait_window=10,
    ):
        parent_type.__init__(self)

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

    def throttler(self, request, **_):
        if self.method(request) == "HEAD" and self._is_too_many_requests():
            resp = Response()
            resp.request = request
            resp.url = request.full_url
            resp.status_code = 429
            resp.reason = (
                "Client throttled to {self.rate:.1f} requests per second"
            )
            return resp

    @staticmethod
    def method(request):
        """Return a string indicating the HTTP request method."""
        default_method = "POST" if getattr(request, "data", None) else "GET"
        return getattr(request, "method", default_method)

    def send(self, *args, **kwargs):
        resp = self.throttler(*args, **kwargs)
        return resp if resp else super().send(*args, **kwargs)

    def open(self, *args, **kwargs):
        resp = self.throttler(*args, **kwargs)
        return resp if resp else super().open(*args, **kwargs)


class RequestsSession(Session):
    def __init__(self, api_host, headers, throttling, proxy, encoding):
        super().__init__()

        self.headers.update(headers)
        self.proxies.update({"http": proxy, "https": proxy})
        self.encoding = encoding

        adapter = HTTPThrottler(HTTPAdapter, **throttling)
        self.mount(api_host, adapter)

    def _get(self, *args, **kwargs):
        return super().get(*args, **kwargs)

    def get_request(self, *args, **kwargs):
        return jsonable(self._get(*args, **kwargs))

    def get_page_data(self, *args, **kwargs):
        return self._get(*args, **kwargs).content.decode(self.encoding)


class UrllibSession:
    """ Urllib HTTP session class with cookies and proxies """

    opener = None
    _client_id = None
    proxy_support = None

    cookie_processor = HTTPCookieProcessor(CookieJar())

    opener_default_classes = [
        ProxyHandler,
        UnknownHandler,
        HTTPHandler,
        HTTPDefaultErrorHandler,
        HTTPRedirectHandler,
        FTPHandler,
        FileHandler,
        HTTPErrorProcessor,
        DataHandler,
    ]

    def __init__(self, headers, throttling, proxy, encoding):
        self.headers = headers
        self.throttling = throttling
        self.proxies = {"http": proxy, "https": proxy}
        self.encoding = encoding

    @property
    def proxies(self):
        return self._proxies

    @proxies.setter
    def proxies(self, proxies):
        self.proxy_support = ProxyHandler(proxies)
        self.build_opener()
        self._proxies = proxies

    def get_request(self, url, params=None):
        if params is not None:
            param_parts = [f"{str(v1)}={str(v2)}" for (v1, v2) in params]
            url += f"?{'&'.join(param_parts)}"
        request = Request(url, headers=self.headers)
        return self._request(request)

    def get_page_data(self, *args, **kwargs):
        return self.get_request(*args, **kwargs).data

    def build_opener(self):
        # See urllib.request.build_opener
        """Create an opener object from a list of handlers.

        The opener will use several default handlers, including support
        for HTTP, FTP and when applicable HTTPS.

        If any of the handlers passed as arguments are subclasses of the
        default handlers, the default handlers will not be used.
        """
        handlers = [self.cookie_processor, self.proxy_support]
        self.opener = HTTPThrottler(OpenerDirector, **self.throttling)
        if hasattr(http.client, "HTTPSConnection"):
            self.opener_default_classes.append(HTTPSHandler)
        skip = set()
        for klass in self.opener_default_classes:
            for check in handlers:
                if isinstance(check, type):
                    if issubclass(check, klass):
                        skip.add(klass)
                elif isinstance(check, klass):
                    skip.add(klass)
        for klass in skip:
            self.opener_default_classes.remove(klass)

        for klass in self.opener_default_classes:
            self.opener.add_handler(klass())

        for h in handlers:
            if isinstance(h, type):
                h = h()
            self.opener.add_handler(h)

    def head(self, url):
        request = Request(url, headers=self.headers, method="HEAD")
        return self._request(request)

    def post(self, url, data=None):
        post_data = urllib.parse.urlencode(data).encode()
        request = Request(url, post_data, self.headers)
        return self._request(request)

    def _request(self, request):
        # install_opener(self.opener)
        response = self.opener.open(request)
        response.status_code = response.getcode()

        encoding = self.encoding
        response.data = response.read().decode(encoding)  # content.decode()?

        return jsonable(response)


def jsonable(response):
    if response.status_code not in auth_error_codes:
        if hasattr(response, "json"):
            response.json = response.json()
        else:
            response.json = json.loads(response.data)
    return response


def raise_for_status(response):
    # See requests.models.Response.raise_for_status()
    """Raises stored :class:`HTTPError`, if one occurred."""

    msg = ""
    if isinstance(response.reason, bytes):
        # We attempt to decode utf-8 first because some servers
        # choose to localize their reason strings. If the string
        # isn't utf-8, we fall back to iso-8859-1 for all other
        # encodings. (See PR #3538)
        try:
            reason = response.reason.decode("utf-8")
        except UnicodeDecodeError:
            reason = response.reason.decode("iso-8859-1")
    else:
        reason = response.reason

    if 400 <= response.status_code < 500:
        msg = f"{response.status_code} Client Error: {reason} for url: {response.url}"

    elif 500 <= response.status_code < 600:
        msg = f"{response.status_code} Server Error: {reason} for url: {response.url}"

    if msg:
        raise HTTPError(msg, response=response)
