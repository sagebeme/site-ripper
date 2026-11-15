"""Walk a website page by page and save an offline copy.

Phase 1 downloads pages and their files, saving each as it arrives.
Phase 2 rewrites the links inside the saved HTML and CSS so they point at the
saved copies; links to anything not saved point at the live web instead.
"""

import hashlib
import posixpath
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from site_ripper.fetcher import Blocked, Fetcher, Refused
from site_ripper.packager import Entry, Result, Skip
from site_ripper.paths import local_path, normalize, relative_link
from site_ripper.rewriter import Kind, looks_script_built, rewrite_css, rewrite_html

MB = 1024 * 1024
MAX_QUERY = 100
"""Links with longer query strings are usually endless filters or calendars."""

Event = dict[str, object]


@dataclass(frozen=True)
class Limits:
    """How big a crawl may get. Defaults match PLAN.md."""

    max_pages: int = 200
    max_depth: int = 3
    max_total_bytes: int = 500 * MB
    max_file_bytes: int = 25 * MB
    max_seconds: float = 30 * 60
    whole_host: bool = False
    """False keeps the crawl inside the pasted folder (``/docs/``)."""

    def clamped(self) -> "Limits":
        """Return a copy pulled inside the hard ceilings (1,000 pages, 2 GB, ...)."""
        total = min(max(self.max_total_bytes, MB), 2048 * MB)
        return replace(
            self,
            max_pages=min(max(self.max_pages, 1), 1000),
            max_depth=min(max(self.max_depth, 0), 10),
            max_total_bytes=total,
            max_file_bytes=min(max(self.max_file_bytes, 1), total),
            max_seconds=min(max(self.max_seconds, 1), 2 * 60 * 60),
        )


def crawl(
    start_url: str,
    out_dir: Path,
    fetcher: Fetcher,
    limits: Limits,
    on_event: Callable[[Event], None] = lambda event: None,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Result:
    """Copy the site at ``start_url`` into ``out_dir``.

    Args:
        start_url: The pasted link.
        out_dir: An empty folder to save into.
        fetcher: Does the polite downloading.
        limits: Caps on pages, depth, size and time.
        on_event: Gets a small dict for each file saved or skipped (for progress).
        stop: Set it from another thread to end the crawl early.
        clock: Swappable for tests.

    Returns:
        What was saved and skipped, and why the crawl ended.
    """
    start = normalize(start_url)
    home = urlsplit(start).hostname or ""
    scope = "/" if limits.whole_host else urlsplit(start).path.rpartition("/")[0] + "/"
    result = Result(source=start, out_dir=out_dir)
    queue: deque[tuple[str, Kind, int]] = deque([(start, "page", 0)])
    seen = {start}
    saved_paths: dict[str, str] = {}  # normalized URL -> relative path
    owners: dict[str, str] = {}  # relative path -> URL that owns it
    blocked: set[str] = set()
    pages, deadline = 0, clock() + limits.max_seconds

    def in_scope(url: str) -> bool:
        parts = urlsplit(url)
        return parts.hostname == home and (parts.path or "/").startswith(scope)

    def skip(url: str, reason: str) -> None:
        result.skipped.append(Skip(url, reason))
        on_event({"type": "skipped", "url": url, "reason": reason})

    def enqueue(url: str, kind: Kind, depth: int) -> None:
        url = normalize(url)
        if url in seen:
            return
        if kind == "page" and not (in_scope(url) and depth <= limits.max_depth):
            return  # left pointing at the live web
        if len(urlsplit(url).query) > MAX_QUERY:
            return
        seen.add(url)
        queue.append((url, kind, depth))

    while True:
        if not queue:
            result.stop_reason = "finished: nothing left to fetch"
            break
        if stop is not None and stop.is_set():
            result.stop_reason = "stopped by you"
            break
        if clock() > deadline:
            result.stop_reason = "time limit reached"
            break
        url, kind, depth = queue.popleft()
        host = urlsplit(url).hostname or ""
        if kind == "page" and pages >= limits.max_pages:
            skip(url, "page limit reached")
            continue
        if host in blocked:
            skip(url, "this site stopped us earlier")
            continue
        try:
            got = fetcher.get(url)
        except Blocked as exc:
            blocked.add(host)
            skip(url, str(exc))
            if host == home:
                result.stop_reason = f"the site stopped us: {exc}"
                break
            continue
        except Refused as exc:
            skip(url, str(exc))
            continue

        final = normalize(got.url)
        is_html = "html" in got.content_type
        if kind == "page" and not is_html:
            kind = "asset"  # e.g. a link to a PDF
        if kind == "page" and not in_scope(final):
            skip(url, "redirected away from the site")
            continue
        if result.total_bytes + len(got.body) > limits.max_total_bytes:
            result.stop_reason = "total size limit reached"
            break

        path = _unique_path(
            local_path(
                final, is_page=kind == "page", home_host=home, content_type=got.content_type
            ),
            final,
            owners,
        )
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(got.body)
        saved_paths[url] = saved_paths[final] = path
        seen.add(final)
        result.saved.append(Entry(final, path, got.content_type, len(got.body)))
        pages += kind == "page"
        on_event(
            {
                "type": "saved",
                "url": final,
                "path": path,
                "size": len(got.body),
                "pages": pages,
                "max_pages": limits.max_pages,
            }
        )

        def found(link: str, link_kind: Kind, depth: int = depth) -> None:
            enqueue(link, link_kind, depth + 1)

        if is_html:
            rewrite_html(got.body, final, found)
            if kind == "page" and looks_script_built(got.body):
                result.warnings.append(
                    f"{final} looks like it is built by JavaScript; the copy may be empty."
                )
        elif "css" in got.content_type:
            rewrite_css(got.body, final, found)

    _rewrite_saved(result, saved_paths)
    on_event({"type": "done", "reason": result.stop_reason})
    return result


def _unique_path(path: str, url: str, owners: dict[str, str]) -> str:
    """Give ``url`` its own path, adding a short hash if another URL already has it."""
    if owners.setdefault(path, url) != url:
        stem, ext = posixpath.splitext(path)
        path = f"{stem}_{hashlib.sha1(url.encode()).hexdigest()[:8]}{ext}"
        owners[path] = url
    return path


def _rewrite_saved(result: Result, saved_paths: dict[str, str]) -> None:
    """Point every link in the saved HTML and CSS at the local copies."""
    for entry in result.saved:
        is_html = "html" in entry.content_type and entry.path.endswith((".html", ".htm"))
        if not (is_html or "css" in entry.content_type):
            continue

        def to_local(url: str, kind: Kind, here: str = entry.path) -> str:
            target = saved_paths.get(normalize(url))
            return relative_link(here, target) if target else url

        file = result.out_dir / entry.path
        rewrite = rewrite_html if is_html else rewrite_css
        file.write_bytes(rewrite(file.read_bytes(), entry.url, to_local))
