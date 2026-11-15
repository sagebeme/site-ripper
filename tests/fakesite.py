"""A fake internet for tests: a dict of URL -> response, served without any network."""

import httpx

from site_ripper.fetcher import Fetcher

Route = tuple[int, dict[str, str], bytes]


class FakeWeb:
    """Serves ``routes`` and records every request it sees."""

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.now = 0.0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, headers, body = self.routes.get(str(request.url), (404, {}, b"not found"))
        return httpx.Response(status, headers=headers, content=body)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def fetcher(self, **kwargs: object) -> Fetcher:
        """A Fetcher wired to this fake web, with no real waiting."""
        return Fetcher(
            transport=httpx.MockTransport(self.handle),
            allow_private=True,
            sleep=self.sleep,
            clock=lambda: self.now,
            **kwargs,  # type: ignore[arg-type]
        )

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def html(body: str) -> Route:
    return (200, {"content-type": "text/html; charset=utf-8"}, body.encode())


def file(body: bytes, content_type: str) -> Route:
    return (200, {"content-type": content_type}, body)
