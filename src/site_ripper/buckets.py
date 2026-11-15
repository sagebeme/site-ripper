"""List and download files from public cloud storage buckets.

Only the exact bucket link the user pastes is used: no guessing names, no
scanning. No keys or logins: if the owner hasn't made the file list public,
we say so and stop.
"""

import base64
import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from site_ripper.fetcher import Fetcher, Refused
from site_ripper.packager import Entry, Result, Skip
from site_ripper.paths import safe_relpath, safe_segment

MAX_LISTED = 10_000
Event = dict[str, object]

Style = Literal["s3", "gcs", "azure"]

# (provider name, host pattern, style, True if the bucket name is in the host)
_HOSTS: list[tuple[str, re.Pattern[str], Style, bool]] = [
    ("Amazon S3", re.compile(r"^(?P<b>.+?)\.s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$"), "s3", True),
    ("Amazon S3", re.compile(r"^s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$"), "s3", False),
    ("Google Cloud Storage", re.compile(r"^(?P<b>.+)\.storage\.googleapis\.com$"), "gcs", True),
    ("Google Cloud Storage", re.compile(r"^storage\.googleapis\.com$"), "gcs", False),
    ("Azure Blob Storage", re.compile(r"^[a-z0-9]+\.blob\.core\.windows\.net$"), "azure", False),
    ("Backblaze B2", re.compile(r"^(?P<b>.+)\.s3\.[a-z0-9-]+\.backblazeb2\.com$"), "s3", True),
    ("Backblaze B2", re.compile(r"^s3\.[a-z0-9-]+\.backblazeb2\.com$"), "s3", False),
    ("Wasabi", re.compile(r"^(?P<b>.+)\.s3\.(?:[a-z0-9-]+\.)?wasabisys\.com$"), "s3", True),
    ("Wasabi", re.compile(r"^s3\.(?:[a-z0-9-]+\.)?wasabisys\.com$"), "s3", False),
    (
        "DigitalOcean Spaces",
        re.compile(r"^(?P<b>.+)\.[a-z0-9-]+\.digitaloceanspaces\.com$"),
        "s3",
        True,
    ),
]

_NOT_LISTABLE = [
    (
        re.compile(r"\.r2\.dev$"),
        "Cloudflare R2 public links serve single files but never a file list. "
        "Paste a web page that links to the files instead.",
    ),
    (
        re.compile(r"^f\d+\.backblazeb2\.com$"),
        "Backblaze 'friendly' links can't list files. Paste the bucket's S3 address "
        "(like https://s3.us-west-004.backblazeb2.com/<bucket>/) instead.",
    ),
]


class NotListable(Exception):
    """The link is a bucket, but its provider never shares a file list."""


@dataclass(frozen=True)
class Bucket:
    """Where a bucket lives and how to ask it for its file list."""

    provider: str
    style: Style
    endpoint: str
    """S3/Azure: the address that answers list requests. GCS: unused."""
    name: str
    prefix: str = ""
    """Only list files whose names start with this (a "folder" inside the bucket)."""

    def file_url(self, key: str) -> str:
        """The public download address of one file."""
        if self.style == "gcs":
            return f"https://storage.googleapis.com/{self.name}/{quote(key, safe='/~')}"
        return self.endpoint.rstrip("/") + "/" + quote(key, safe="/~")


@dataclass(frozen=True)
class BucketFile:
    """One file in a bucket listing."""

    key: str
    size: int
    modified: str
    md5: str
    """Hex MD5 of the contents when the provider gives one, else empty."""
    url: str


def detect(url: str, *, force_s3: bool = False) -> Bucket | None:
    """Recognise a bucket link.

    Args:
        url: The pasted link (``https://``, ``s3://`` or ``gs://``).
        force_s3: Treat any https link as an S3-compatible bucket address
            (for MinIO and other self-hosted services).

    Returns:
        The bucket, or None if the link isn't one.

    Raises:
        NotListable: it is a bucket, but the provider can't list files publicly.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if parts.scheme == "s3" and host:
        return Bucket("Amazon S3", "s3", f"https://{host}.s3.amazonaws.com/", host, parts.path[1:])
    if parts.scheme == "gs" and host:
        return Bucket("Google Cloud Storage", "gcs", "", host, parts.path[1:])
    for pattern, message in _NOT_LISTABLE:
        if pattern.search(host):
            raise NotListable(message)
    if parts.scheme not in ("http", "https"):
        return None
    for provider, pattern, style, name_in_host in _HOSTS:
        match = pattern.match(host)
        if not match:
            continue
        if name_in_host:
            prefix = "/".join(segments)
            return Bucket(provider, style, f"https://{host}/", match["b"], prefix)
        if not segments:
            return None
        name, prefix = segments[0], "/".join(segments[1:])
        return Bucket(provider, style, f"https://{host}/{name}", name, prefix)
    if force_s3:
        return Bucket("S3-compatible", "s3", f"https://{parts.netloc}/{'/'.join(segments)}", host)
    return None


def list_files(
    bucket: Bucket, fetcher: Fetcher, max_files: int = MAX_LISTED
) -> tuple[list[BucketFile], bool]:
    """Ask the bucket for its file list, one page at a time.

    Returns:
        The files (up to ``max_files``) and True if more were left unlisted.

    Raises:
        Refused: the owner doesn't share the list, or the bucket doesn't exist.
    """
    files: list[BucketFile] = []
    token = ""
    while len(files) < max_files:
        try:
            got = fetcher.get(_list_url(bucket, token))
        except Refused as exc:
            if "login" in str(exc):
                raise Refused("this bucket doesn't share its file list publicly") from exc
            raise
        try:
            page, token = _parse(bucket, got.body)
        except (ValueError, KeyError, ElementTree.ParseError) as exc:
            raise Refused("the answer wasn't a bucket file list") from exc
        files.extend(page)
        if not token:
            return files[:max_files], False
    return files[:max_files], True


def download(
    bucket: Bucket,
    files: list[BucketFile],
    out_dir: Path,
    fetcher: Fetcher,
    max_total_bytes: int,
    on_event: Callable[[Event], None] = lambda event: None,
    stop: threading.Event | None = None,
) -> Result:
    """Download the chosen files into ``out_dir/<bucket name>/``.

    Each file is checked against the listing: same size, and the same MD5
    when the provider gives one. Files that don't match are not kept.
    """
    result = Result(source=f"{bucket.provider} bucket {bucket.name}", out_dir=out_dir)
    result.stop_reason = "finished: all chosen files handled"
    for item in files:
        if stop is not None and stop.is_set():
            result.stop_reason = "stopped by you"
            break
        if item.size > fetcher.max_file_bytes:
            _skip(result, on_event, item.url, f"too big ({item.size} bytes)")
            continue
        if result.total_bytes + item.size > max_total_bytes:
            result.stop_reason = "total size limit reached"
            break
        try:
            got = fetcher.get(item.url)
        except Refused as exc:
            _skip(result, on_event, item.url, str(exc))
            continue
        problem = _check(item, got.body)
        if problem:
            _skip(result, on_event, item.url, problem)
            continue
        path = f"{safe_segment(bucket.name)}/{safe_relpath(item.key)}"
        (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / path).write_bytes(got.body)
        result.saved.append(Entry(item.url, path, got.content_type, len(got.body)))
        on_event({"type": "saved", "url": item.url, "path": path, "size": len(got.body)})
    on_event({"type": "done", "reason": result.stop_reason})
    return result


def _skip(result: Result, on_event: Callable[[Event], None], url: str, reason: str) -> None:
    result.skipped.append(Skip(url, reason))
    on_event({"type": "skipped", "url": url, "reason": reason})


def _check(item: BucketFile, body: bytes) -> str:
    if len(body) != item.size:
        return f"size doesn't match the list ({len(body)} vs {item.size} bytes)"
    if item.md5 and hashlib.md5(body).hexdigest() != item.md5:
        return "contents don't match the bucket's checksum"
    return ""


def _list_url(bucket: Bucket, token: str) -> str:
    if bucket.style == "gcs":
        params = {"prefix": bucket.prefix, "pageToken": token, "maxResults": "1000"}
        base = f"https://storage.googleapis.com/storage/v1/b/{quote(bucket.name, safe='')}/o"
    elif bucket.style == "azure":
        params = {"restype": "container", "comp": "list", "prefix": bucket.prefix, "marker": token}
        base = bucket.endpoint
    else:
        params = {"list-type": "2", "prefix": bucket.prefix, "continuation-token": token}
        base = bucket.endpoint
    return f"{base}?{urlencode({k: v for k, v in params.items() if v})}"


def _parse(bucket: Bucket, body: bytes) -> tuple[list[BucketFile], str]:
    """Turn one page of a listing into files plus the token for the next page."""
    if bucket.style == "gcs":
        data = json.loads(body)
        files = [
            BucketFile(
                key=item["name"],
                size=int(item.get("size", 0)),
                modified=item.get("updated", ""),
                md5=_b64_to_hex(item.get("md5Hash", "")),
                url=bucket.file_url(item["name"]),
            )
            for item in data.get("items", [])
            if not item["name"].endswith("/")
        ]
        return files, data.get("nextPageToken", "")

    root = ElementTree.fromstring(body)
    if bucket.style == "azure":
        files = [
            BucketFile(
                key=_text(blob, "Name"),
                size=int(_text(blob, "Content-Length") or 0),
                modified=_text(blob, "Last-Modified"),
                md5=_b64_to_hex(_text(blob, "Content-MD5")),
                url=bucket.file_url(_text(blob, "Name")),
            )
            for blob in _all(root, "Blob")
        ]
        return files, _text(root, "NextMarker")

    files = []
    for item in _all(root, "Contents"):
        key = _text(item, "Key")
        if key.endswith("/"):
            continue  # a "folder" marker, not a file
        etag = _text(item, "ETag").strip('"')
        md5 = etag if re.fullmatch(r"[0-9a-f]{32}", etag) else ""  # multipart ETags aren't MD5
        modified = _text(item, "LastModified")
        size = int(_text(item, "Size") or 0)
        files.append(BucketFile(key, size, modified, md5, bucket.file_url(key)))
    truncated = _text(root, "IsTruncated") == "true"
    return files, (_text(root, "NextContinuationToken") if truncated else "")


def _all(root: Element, tag: str) -> list[Element]:
    """Find elements by name, ignoring XML namespaces."""
    return [el for el in root.iter() if el.tag.rsplit("}", 1)[-1] == tag]


def _text(root: Element, tag: str) -> str:
    found = _all(root, tag)
    return (found[0].text or "").strip() if found else ""


def _b64_to_hex(value: str) -> str:
    try:
        return base64.b64decode(value).hex() if value else ""
    except ValueError:
        return ""
