"""Turn URLs into safe, relative file paths inside the output folder."""

import hashlib
import mimetypes
import posixpath
import re
from urllib.parse import unquote, urldefrag, urlsplit

_UNSAFE = re.compile(r"[^A-Za-z0-9._~-]+")
_MAX_SEGMENT = 100
_PAGE_EXTS = (".html", ".htm")


def normalize(url: str) -> str:
    """Return ``url`` without its #fragment and with a lower-case scheme and host.

    Two links that only differ in these ways point at the same file, so the
    crawler uses this as its "have I seen this?" key.
    """
    url, _ = urldefrag(url.strip())
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{netloc}{path}{query}"


def safe_segment(text: str) -> str:
    """Make one path segment safe for any file system.

    Odd characters become ``_``, ``.``/``..`` can't climb out of the folder,
    and very long names are cut short with a hash so they stay unique.
    """
    clean = _UNSAFE.sub("_", unquote(text)).strip(".") or "_"
    if len(clean) > _MAX_SEGMENT:
        digest = hashlib.sha1(clean.encode()).hexdigest()[:8]
        stem, ext = posixpath.splitext(clean)
        clean = f"{stem[: _MAX_SEGMENT - 20]}_{digest}{ext[:10]}"
    return clean


def safe_relpath(path: str) -> str:
    """Make a ``/``-separated path safe, segment by segment (empty parts dropped)."""
    return "/".join(safe_segment(p) for p in path.split("/") if p not in ("", ".", ".."))


def local_path(url: str, *, is_page: bool, home_host: str, content_type: str = "") -> str:
    """Pick where a downloaded URL is saved, relative to the output folder.

    Args:
        url: The absolute URL that was downloaded.
        is_page: True for HTML pages, which always end in ``.html`` so a
            browser opens them offline.
        home_host: Host of the site being copied. Files from any other host
            (CDNs, buckets) go under ``_assets/<host>/``.
        content_type: The server's Content-Type, used to add a missing
            file extension (``/logo`` served as PNG becomes ``logo.png``).

    Returns:
        A relative POSIX path such as ``example.com/docs/index.html``.
    """
    parts = urlsplit(url)
    host = safe_segment(parts.hostname or "unknown-host")
    prefix = host if parts.hostname == home_host else f"_assets/{host}"
    dirs, _, name = parts.path.rpartition("/")
    dirs = safe_relpath(dirs)
    name = safe_segment(name) if name else ""
    stem, ext = posixpath.splitext(name)
    if parts.query:
        stem = f"{stem or 'index'}_{safe_segment(parts.query)}"

    if is_page:
        if not name:
            name = f"{stem or 'index'}.html"
        elif ext.lower() in _PAGE_EXTS:
            name = f"{stem}{ext}"
        elif ext:
            name = f"{stem}{ext}.html"
        else:
            dirs, name = posixpath.join(dirs, stem), "index.html"
    else:
        mime = content_type.split(";")[0].strip()
        if not ext and mime:
            ext = mimetypes.guess_extension(mime) or ""
        name = f"{stem or 'index'}{ext}"
    return posixpath.join(prefix, dirs, name) if dirs else posixpath.join(prefix, name)


def relative_link(from_file: str, to_file: str) -> str:
    """Return the relative link from one saved file to another (both output-relative)."""
    return posixpath.relpath(to_file, posixpath.dirname(from_file) or ".")
