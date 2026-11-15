import pytest

from site_ripper import USER_AGENT
from site_ripper.fetcher import Blocked, Fetcher, Refused
from tests.fakesite import FakeWeb, Route, file, html

ROBOTS = (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private/\n")


def test_sends_honest_user_agent_and_no_cookies() -> None:
    web = FakeWeb(
        {
            "https://a.test/": (200, {"set-cookie": "sid=1", "content-type": "text/html"}, b"hi"),
            "https://a.test/b": html("b"),
        }
    )
    fetcher = web.fetcher()
    fetcher.get("https://a.test/")
    fetcher.get("https://a.test/b")
    assert all(r.headers["user-agent"] == USER_AGENT for r in web.requests)
    assert all("cookie" not in r.headers for r in web.requests)


def test_obeys_robots_txt() -> None:
    web = FakeWeb({"https://a.test/robots.txt": ROBOTS, "https://a.test/private/x": html("no")})
    with pytest.raises(Refused, match="robots"):
        web.fetcher().get("https://a.test/private/x")
    assert "https://a.test/private/x" not in web.urls()


def test_robots_server_error_means_fetch_nothing() -> None:
    web = FakeWeb({"https://a.test/robots.txt": (500, {}, b""), "https://a.test/": html("x")})
    with pytest.raises(Refused, match="robots"):
        web.fetcher().get("https://a.test/")


def test_waits_between_requests_and_honours_crawl_delay() -> None:
    robots: Route = (200, {}, b"User-agent: *\nCrawl-delay: 5\n")
    web = FakeWeb({"https://a.test/robots.txt": robots, "https://a.test/1": html("1")})
    fetcher = web.fetcher(delay=1.0)
    fetcher.get("https://a.test/1")
    fetcher.get("https://a.test/1")
    assert web.slept and max(web.slept) == pytest.approx(5.0)


def test_refuses_files_over_the_size_cap() -> None:
    web = FakeWeb({"https://a.test/big.bin": file(b"x" * 2000, "application/octet-stream")})
    with pytest.raises(Refused, match="too big"):
        web.fetcher(max_file_bytes=1000).get("https://a.test/big.bin")


def test_login_and_paywall_pages_are_skipped_not_bypassed() -> None:
    web = FakeWeb({"https://a.test/members": (401, {}, b"log in")})
    with pytest.raises(Refused, match="login or payment"):
        web.fetcher().get("https://a.test/members")
    assert len([u for u in web.urls() if u.endswith("/members")]) == 1


def test_captcha_stops_the_host() -> None:
    page = b'<div class="g-recaptcha"></div>'
    web = FakeWeb({"https://a.test/": (403, {"content-type": "text/html"}, page)})
    with pytest.raises(Blocked):
        web.fetcher().get("https://a.test/")


def test_backs_off_then_gives_up_on_too_many_requests() -> None:
    web = FakeWeb({"https://a.test/": (429, {"retry-after": "7"}, b"")})
    with pytest.raises(Blocked, match="slow down"):
        web.fetcher().get("https://a.test/")
    assert web.slept.count(7.0) == 2


def test_follows_redirects_and_reports_final_url() -> None:
    web = FakeWeb(
        {
            "https://a.test/old": (301, {"location": "/new"}, b""),
            "https://a.test/new": html("new"),
        }
    )
    assert web.fetcher().get("https://a.test/old").url == "https://a.test/new"


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://localhost/", "http://10.0.0.1/", "file:///etc/passwd"]
)
def test_refuses_local_and_private_addresses(url: str) -> None:
    with pytest.raises(Refused):
        Fetcher().get(url)
