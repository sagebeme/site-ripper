from site_ripper.rewriter import Kind, looks_script_built, rewrite_css, rewrite_html

BASE = "https://example.com/docs/page.html"


def collect(body: bytes) -> list[tuple[str, Kind]]:
    found: list[tuple[str, Kind]] = []

    def record(url: str, kind: Kind) -> None:
        found.append((url, kind))

    rewrite_html(body, BASE, record)
    return found


def test_finds_pages_and_assets() -> None:
    body = b"""<html><head>
      <link rel="stylesheet" href="../css/site.css">
      <link rel="canonical" href="/canonical">
      <script src="app.js"></script></head>
      <body><a href="next.html#part">next</a><img src="a.png" srcset="b.png 2x, c.png 3x">
      <a href="mailto:x@example.com">mail</a><div style="background: url('bg.jpg')"></div>
      </body></html>"""
    found = collect(body)
    assert ("https://example.com/css/site.css", "asset") in found
    assert ("https://example.com/docs/app.js", "asset") in found
    assert ("https://example.com/docs/next.html", "page") in found
    assert ("https://example.com/docs/c.png", "asset") in found
    assert ("https://example.com/docs/bg.jpg", "asset") in found
    assert not any("canonical" in url or "mailto" in url for url, _ in found)


def test_base_tag_changes_resolution_and_is_removed() -> None:
    body = b'<head><base href="https://cdn.example.net/x/"></head><img src="a.png">'
    out = rewrite_html(body, BASE, lambda url, kind: "local/" + url.rsplit("/", 1)[1])
    assert b"<base" not in out
    assert b'src="local/a.png"' in out


def test_rewrites_keep_fragment_and_drop_integrity() -> None:
    body = b'<a href="next.html#top">n</a>' + (
        b'<script src="a.js" integrity="sha-x" crossorigin></script>'
    )
    out = rewrite_html(body, BASE, lambda url, kind: "saved")
    assert b'href="saved#top"' in out
    assert b"integrity" not in out and b"crossorigin" not in out


def test_unchanged_links_stay_as_they_were() -> None:
    body = b'<a href="https://other.example.org/">x</a>'
    assert b'href="https://other.example.org/"' in rewrite_html(body, BASE, lambda u, k: None)


def test_css_urls_and_imports_are_rewritten() -> None:
    css = b'@import "theme.css";\nbody{background:url(img/bg.png)} @font-face{src:url("f.woff2")}'
    out = rewrite_css(css, "https://example.com/css/site.css", lambda url, kind: "L")
    assert out == b'@import "L";\nbody{background:url("L")} @font-face{src:url("L")}'


def test_css_data_uris_are_left_alone() -> None:
    css = b"a{background:url(data:image/png;base64,AAAA)}"
    assert rewrite_css(css, BASE, lambda url, kind: "L") == css


def test_detects_pages_built_by_javascript() -> None:
    assert looks_script_built(b'<div id="root"></div><script src="app.js"></script>')
    assert not looks_script_built(b"<p>" + b"Real words on the page. " * 10 + b"</p>")
