"""Download one URL at a time, politely.

Every rule here is always on: robots.txt, a delay between requests to the same
host, an honest User-Agent, no cookies or logins, a per-file size cap, and no
requests to private or local network addresses.
"""

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from site_ripper import ROBOTS_NAME, USER_AGENT

MIN_DELAY = 1.0
MAX_CRAWL_DELAY = 60.0
MAX_REDIRECTS = 5
MAX_RETRIES = 2

# Pages that are a "prove you're human" wall rather than real content.
_CHALLENGE_MARKERS = (b"/cdn-cgi/challenge-platform", b"cf-chl-", b"captcha-delivery.com")
# On an error page (403/429/503) any CAPTCHA widget means we've been stopped.
_CAPTCHA_MARKERS = (*_CHALLENGE_MARKERS, b"g-recaptcha", b"h-captcha", b"cf-turnstile")


class Refused(Exception):
    """This one URL can't or mustn't be downloaded; ``str(exc)`` says why."""


class Blocked(Refused):
    """The whole host has stopped us (CAPTCHA, or it keeps saying "too many requests")."""


@dataclass
class Fetched:
    """A successful download."""

    url: str
    """Final URL after redirects."""
    content_type: str
    body: bytes


class Fetcher:
    """Polite HTTP downloader shared by the crawler and the bucket tools."""

    def __init__(
        self,
        *,
        delay: float = MIN_DELAY,
        max_file_bytes: int = 25 * 1024 * 1024,
        timeout: float = 20.0,
        allow_private: bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Set up the downloader.

        Args:
            delay: Seconds between requests to one host. Callers facing users
                must not go below ``MIN_DELAY``; tests may.
            max_file_bytes: Larger files are refused without being stored.
            timeout: Seconds to wait for a server before giving up.
            allow_private: Only for tests that serve a fake site on localhost.
            transport: Optional fake network for tests.
            sleep, clock: Swappable for tests so they don't really wait.
        """
        self.delay = delay
        self.max_file_bytes = max_file_bytes
        self.allow_private = allow_private
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._host_delay: dict[str, float] = {}
        self._last_request: dict[str, float] = {}

    def close(self) -> None:
        """Close the network connections."""
        self._client.close()

    def allowed(self, url: str) -> bool:
        """Return True if the site's robots.txt lets us fetch ``url``."""
        origin = _origin(url)
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        robots = self._robots[origin]
        # None means robots.txt couldn't be read because the server failed:
        # play safe and fetch nothing from that host.
        return robots is not None and robots.can_fetch(ROBOTS_NAME, url)

    def get(self, url: str) -> Fetched:
        """Download ``url``, following up to ``MAX_REDIRECTS`` redirects.

        Raises:
            Refused: robots.txt says no, the address is private, the file is
                too big, the server answered with an error, or it needs a
                login or payment.
            Blocked: the host showed a CAPTCHA or kept refusing.
        """
        for _ in range(MAX_REDIRECTS + 1):
            self._check_address(url)
            if not self.allowed(url):
                raise Refused("robots.txt asks robots not to fetch this")
            response = self._request(url)
            if response.is_redirect and "location" in response.headers:
                response.close()
                url = urljoin(url, response.headers["location"])
                continue
            return self._read(url, response)
        raise Refused("too many redirects")

    def _read(self, url: str, response: httpx.Response) -> Fetched:
        try:
            content_type = response.headers.get("content-type", "")
            if response.status_code in (401, 402, 403):
                if _has_marker(response.read(), _CAPTCHA_MARKERS):
                    raise Blocked("the site showed a CAPTCHA; we don't solve those")
                raise Refused(f"needs a login or payment (HTTP {response.status_code})")
            if response.status_code >= 400:
                raise Refused(f"server error (HTTP {response.status_code})")
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > self.max_file_bytes:
                raise Refused(f"too big ({int(length)} bytes)")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.max_file_bytes:
                    raise Refused(f"too big (over {self.max_file_bytes} bytes)")
                chunks.append(chunk)
        finally:
            response.close()
        body = b"".join(chunks)
        if "html" in content_type and _has_marker(body, _CHALLENGE_MARKERS):
            raise Blocked("the site showed a bot check; we don't get around those")
        return Fetched(url=url, content_type=content_type, body=body)

    def _request(self, url: str) -> httpx.Response:
        """Send one GET after the polite wait, backing off on 429/503."""
        host = urlsplit(url).netloc
        for attempt in range(MAX_RETRIES + 1):
            self._wait_turn(host)
            try:
                response = self._client.send(self._client.build_request("GET", url), stream=True)
            except httpx.HTTPError as exc:
                raise Refused(f"network error ({type(exc).__name__})") from exc
            finally:
                self._client.cookies.clear()  # never carry cookies between requests
            if response.status_code not in (429, 503):
                return response
            body = response.read()
            response.close()
            if _has_marker(body, _CAPTCHA_MARKERS):
                raise Blocked("the site showed a CAPTCHA; we don't solve those")
            if attempt < MAX_RETRIES:
                self._sleep(_retry_after(response))
        raise Blocked(f"the site keeps saying 'slow down' (HTTP {response.status_code})")

    def _wait_turn(self, host: str) -> None:
        delay = self._host_delay.get(host, self.delay)
        last = self._last_request.get(host)
        if last is not None:
            wait = last + delay - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """Fetch and parse robots.txt the way Google does.

        A missing file (any 4xx) means everything is allowed; a server error
        or network failure means nothing is (returned as None).
        """
        parser = RobotFileParser()
        try:
            self._check_address(origin)
            response = self._request(f"{origin}/robots.txt")
            try:
                status, text = response.status_code, response.read()[:512_000]
            finally:
                response.close()
        except Refused:
            return None
        if status >= 500:
            return None
        if 200 <= status < 300:
            parser.parse(text.decode("utf-8", "replace").splitlines())
            crawl_delay = parser.crawl_delay(ROBOTS_NAME)
            if crawl_delay:
                wait = min(float(crawl_delay), MAX_CRAWL_DELAY)
                self._host_delay[urlsplit(origin).netloc] = max(self.delay, wait)
        else:
            parser.parse([])  # no rules: everything allowed
        return parser

    def _check_address(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Refused("only http:// and https:// links are supported")
        if self.allow_private:
            return
        try:
            infos = socket.getaddrinfo(parts.hostname, None)
        except socket.gaierror as exc:
            raise Refused("host not found") from exc
        for info in infos:
            if not ipaddress.ip_address(str(info[4][0]).split("%")[0]).is_global:
                raise Refused("refusing a private or local network address")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _has_marker(body: bytes, markers: tuple[bytes, ...]) -> bool:
    head = body[:200_000].lower()
    return any(marker.lower() in head for marker in markers)


def _retry_after(response: httpx.Response) -> float:
    value = response.headers.get("retry-after", "")
    return min(float(value), MAX_CRAWL_DELAY) if value.isdigit() else 10.0
