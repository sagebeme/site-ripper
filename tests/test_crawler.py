import json
import threading
import zipfile
from pathlib import Path

from site_ripper.crawler import Limits, crawl
from site_ripper.packager import finish
from tests.fakesite import FakeWeb, Route, file, html

ROBOTS: Route = (200, {}, b"User-agent: *\nDisallow: /docs/private/\n")


def fake_site() -> FakeWeb:
    return FakeWeb(
        {
            "https://site.test/robots.txt": ROBOTS,
            "https://site.test/docs/": html(
                '<link rel="stylesheet" href="/static/site.css">'
                '<img src="https://cdn.test/logo.png">'
                '<a href="a.html#intro">A</a> <a href="private/secret.html">P</a>'
                '<a href="/blog/">blog, outside /docs/</a> <a href="https://elsewhere.test/">x</a>'
            ),
            "https://site.test/docs/a.html": html('<a href="deep/">deeper</a> <a href="./">up</a>'),
            "https://site.test/docs/deep/": html('<a href="deeper/">even deeper</a>'),
            "https://site.test/docs/deep/deeper/": html("too deep to fetch"),
            "https://site.test/static/site.css": file(
                b"@font-face{src:url(fonts/f.woff2)}", "text/css"
            ),
            "https://site.test/static/fonts/f.woff2": file(b"FONT", "font/woff2"),
            "https://cdn.test/logo.png": file(b"PNG", "image/png"),
        }
    )


def run(tmp_path: Path, web: FakeWeb, **limits: object) -> tuple[Path, list[str]]:
    events: list[str] = []
    result = crawl(
        "https://site.test/docs/",
        tmp_path,
        web.fetcher(),
        Limits(**limits),  # type: ignore[arg-type]
        on_event=lambda e: events.append(str(e["type"])),
    )
    finish(result)
    return tmp_path, events


def test_copies_site_and_rewrites_links(tmp_path: Path) -> None:
    out, events = run(tmp_path, fake_site(), max_depth=2)
    index = (out / "site.test/docs/index.html").read_text()
    assert 'href="../static/site.css"' in index
    assert 'src="../../_assets/cdn.test/logo.png"' in index
    assert 'href="a.html#intro"' in index
    # Not copied, so they keep pointing at the live web:
    assert 'href="https://site.test/blog/"' in index
    assert 'href="https://site.test/docs/private/secret.html"' in index
    css = (out / "site.test/static/site.css").read_text()
    assert css == '@font-face{src:url("fonts/f.woff2")}'
    assert (out / "site.test/static/fonts/f.woff2").read_bytes() == b"FONT"
    assert (out / "site.test/docs/a.html").exists()
    assert events[-1] == "done"


def test_respects_robots_scope_and_depth(tmp_path: Path) -> None:
    web = fake_site()
    run(tmp_path, web, max_depth=2)
    fetched = web.urls()
    assert "https://site.test/docs/private/secret.html" not in fetched
    assert "https://site.test/blog/" not in fetched
    assert "https://elsewhere.test/" not in fetched
    assert "https://site.test/docs/deep/" in fetched
    assert "https://site.test/docs/deep/deeper/" not in fetched


def test_page_limit_and_report(tmp_path: Path) -> None:
    out, _ = run(tmp_path, fake_site(), max_pages=1)
    manifest = json.loads((out / "manifest.json").read_text())
    pages = [e for e in manifest["saved"] if e["path"].endswith(".html")]
    assert len(pages) == 1
    assert any(s["reason"] == "page limit reached" for s in manifest["skipped"])
    assert (
        "Only keep or share this copy if you have the right to." in (out / "REPORT.md").read_text()
    )
    with zipfile.ZipFile(out.with_suffix(".zip")) as archive:
        assert f"{out.name}/site.test/docs/index.html" in archive.namelist()


def test_stop_button_ends_crawl(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    result = crawl("https://site.test/docs/", tmp_path, fake_site().fetcher(), Limits(), stop=stop)
    assert result.stop_reason == "stopped by you"
    assert result.saved == []


def test_limits_are_clamped_to_hard_ceilings() -> None:
    big = Limits(max_pages=10_000, max_depth=99, max_total_bytes=10**12).clamped()
    assert (big.max_pages, big.max_depth, big.max_total_bytes) == (1000, 10, 2048 * 1024 * 1024)
