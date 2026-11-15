"""Find the links inside HTML and CSS, and swap them for new ones.

The same functions do both jobs. While crawling, the callback just records
each link and returns None (keep it). After crawling, the callback returns the
relative path of the saved copy, so the offline copy points at itself.
"""

import re
from collections.abc import Callable
from typing import Literal
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup, Tag

Kind = Literal["page", "asset"]
LinkFn = Callable[[str, Kind], str | None]
"""Called with (absolute URL without #fragment, kind); returns a replacement or None."""

_PAGE_ATTRS = {"a": "href", "area": "href", "iframe": "src", "frame": "src"}
_ASSET_ATTRS = {
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "input": ("src",),
}
_ASSET_RELS = {"stylesheet", "icon", "shortcut", "apple-touch-icon", "mask-icon", "manifest"}
_ASSET_RELS |= {"preload", "modulepreload", "prefetch"}
_SKIP_PREFIXES = ("data:", "javascript:", "mailto:", "tel:", "about:", "blob:", "#")

_CSS_URL = re.compile(r"""url\(\s*(['"]?)([^'")]*?)\1\s*\)""", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"""@import\s+(['"])([^'"]*)\1""", re.IGNORECASE)


def _swap(value: str, base_url: str, kind: Kind, fn: LinkFn) -> str | None:
    """Resolve one link and ask ``fn`` for its replacement (fragment kept)."""
    value = value.strip()
    if not value or value.lower().startswith(_SKIP_PREFIXES):
        return None
    absolute = urljoin(base_url, value)
    if not absolute.startswith(("http://", "https://")):
        return None
    url, fragment = urldefrag(absolute)
    new = fn(url, kind)
    if new is None:
        return None
    return f"{new}#{fragment}" if fragment else new


def rewrite_css_text(css: str, base_url: str, fn: LinkFn) -> str:
    """Rewrite ``url(...)`` and ``@import "..."`` links in a piece of CSS."""

    def url_sub(match: re.Match[str]) -> str:
        new = _swap(match.group(2), base_url, "asset", fn)
        return match.group(0) if new is None else f'url("{new}")'

    def import_sub(match: re.Match[str]) -> str:
        new = _swap(match.group(2), base_url, "asset", fn)
        return match.group(0) if new is None else f'@import "{new}"'

    return _CSS_URL.sub(url_sub, _CSS_IMPORT.sub(import_sub, css))


def rewrite_css(body: bytes, base_url: str, fn: LinkFn) -> bytes:
    """Rewrite links in a CSS file, leaving every other byte as it was."""
    # surrogateescape round-trips any byte, so odd encodings survive untouched.
    text = body.decode("utf-8", "surrogateescape")
    return rewrite_css_text(text, base_url, fn).encode("utf-8", "surrogateescape")


def _srcset(value: str, base_url: str, fn: LinkFn) -> str | None:
    """Rewrite each URL in a ``srcset`` list like ``a.jpg 1x, b.jpg 2x``."""
    changed, parts = False, []
    for candidate in value.split(","):
        url, _, descriptor = candidate.strip().partition(" ")
        new = _swap(url, base_url, "asset", fn) if url else None
        changed |= new is not None
        parts.append(f"{new or url} {descriptor}".strip())
    return ", ".join(parts) if changed else None


def rewrite_html(body: bytes, base_url: str, fn: LinkFn) -> bytes:
    """Rewrite every page and asset link in an HTML document.

    Args:
        body: The raw HTML.
        base_url: The page's own URL (a ``<base href>`` in the page wins).
        fn: Decides each link's replacement; see ``LinkFn``.

    Returns:
        The HTML re-encoded as UTF-8. A ``<base>`` tag is removed, and so are
        ``integrity``/``crossorigin`` on rewritten tags, because the local
        files are edited copies and are opened from disk.
    """
    soup = BeautifulSoup(body, "html.parser")
    base = soup.find("base", href=True)
    if isinstance(base, Tag):
        base_url = urljoin(base_url, str(base["href"]))
        base.decompose()

    for tag in soup.find_all(True):
        for attr, kind in _attrs_for(tag):
            value = str(tag.get(attr, ""))
            new = (
                _srcset(value, base_url, fn)
                if attr == "srcset"
                else _swap(value, base_url, kind, fn)
            )
            if new is not None:
                tag[attr] = new
                for stale in ("integrity", "crossorigin"):
                    if stale in tag.attrs:
                        del tag[stale]
        if tag.get("style"):
            tag["style"] = rewrite_css_text(str(tag["style"]), base_url, fn)
        if tag.name == "style" and tag.string:
            tag.string = rewrite_css_text(tag.string, base_url, fn)
    return soup.encode("utf-8")


def _attrs_for(tag: Tag) -> list[tuple[str, Kind]]:
    if tag.name in _PAGE_ATTRS and tag.get(_PAGE_ATTRS[tag.name]):
        return [(_PAGE_ATTRS[tag.name], "page")]
    if tag.name == "link" and tag.get("href"):
        rels = {str(r).lower() for r in tag.get_attribute_list("rel") if r}
        return [("href", "asset")] if rels & _ASSET_RELS else []
    return [(a, "asset") for a in _ASSET_ATTRS.get(tag.name, ()) if tag.get(a)]


def looks_script_built(body: bytes) -> bool:
    """Guess whether a page is mostly built by JavaScript (little text, has scripts).

    Such pages copy as near-empty shells; the report warns about them.
    """
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    has_script = BeautifulSoup(body, "html.parser").find("script") is not None
    return has_script and len(soup.get_text(" ", strip=True)) < 100
