import base64
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from site_ripper.buckets import Bucket, BucketFile, NotListable, detect, download, list_files
from site_ripper.fetcher import Refused
from tests.fakesite import FakeWeb, Route, file

PDF = b"%PDF-1.3 pretend"
PDF_MD5 = hashlib.md5(PDF).hexdigest()


def s3_page(keys: list[str], token: str = "") -> Route:
    contents = "".join(
        f"<Contents><Key>{escape(k)}</Key><LastModified>2011-11-30T19:52:43.000Z</LastModified>"
        f"<ETag>&quot;{PDF_MD5}&quot;</ETag><Size>{len(PDF)}</Size></Contents>"
        for k in keys
    )
    more = f"<IsTruncated>true</IsTruncated><NextContinuationToken>{token}</NextContinuationToken>"
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>freebies</Name>'
        + (more if token else "<IsTruncated>false</IsTruncated>")
        + contents
        + "</ListBucketResult>"
    )
    return file(body.encode(), "application/xml")


@pytest.mark.parametrize(
    ("url", "provider", "endpoint", "name", "prefix"),
    [
        ("https://freebies.s3.amazonaws.com/", "Amazon S3", "https://freebies.s3.amazonaws.com/",
         "freebies", ""),
        ("https://s3.us-west-2.amazonaws.com/my.data/2024/", "Amazon S3",
         "https://s3.us-west-2.amazonaws.com/my.data", "my.data", "2024"),
        ("s3://freebies/books", "Amazon S3", "https://freebies.s3.amazonaws.com/", "freebies",
         "books"),
        ("https://storage.googleapis.com/gcp-public-data/x", "Google Cloud Storage",
         "https://storage.googleapis.com/gcp-public-data", "gcp-public-data", "x"),
        ("https://acct.blob.core.windows.net/open/", "Azure Blob Storage",
         "https://acct.blob.core.windows.net/open", "open", ""),
        ("https://s3.us-west-004.backblazeb2.com/b2pub/", "Backblaze B2",
         "https://s3.us-west-004.backblazeb2.com/b2pub", "b2pub", ""),
        ("https://space.nyc3.digitaloceanspaces.com/", "DigitalOcean Spaces",
         "https://space.nyc3.digitaloceanspaces.com/", "space", ""),
    ],
)  # fmt: skip
def test_detects_bucket_links(
    url: str, provider: str, endpoint: str, name: str, prefix: str
) -> None:
    bucket = detect(url)
    assert bucket is not None
    assert (bucket.provider, bucket.endpoint, bucket.name, bucket.prefix) == (
        provider, endpoint, name, prefix,
    )  # fmt: skip


def test_ordinary_sites_are_not_buckets() -> None:
    assert detect("https://example.com/docs/") is None


@pytest.mark.parametrize(
    "url", ["https://pub-abc.r2.dev/a.png", "https://f004.backblazeb2.com/file/b/x"]
)
def test_providers_without_public_lists_explain_why(url: str) -> None:
    with pytest.raises(NotListable):
        detect(url)


def test_lists_s3_across_pages_and_downloads_with_checks(tmp_path: Path) -> None:
    base = "https://freebies.s3.amazonaws.com/"
    web = FakeWeb(
        {
            f"{base}?list-type=2": s3_page(["TripleYourIncome.pdf", "folder/"], token="t2"),
            f"{base}?list-type=2&continuation-token=t2": s3_page(["Facebook & Twitter.pdf"]),
            f"{base}TripleYourIncome.pdf": file(PDF, "application/pdf"),
            f"{base}Facebook%20%26%20Twitter.pdf": file(b"tampered!", "application/pdf"),
        }
    )
    fetcher = web.fetcher()
    bucket = detect(base)
    assert bucket is not None
    files, more = list_files(bucket, fetcher)
    assert [f.key for f in files] == ["TripleYourIncome.pdf", "Facebook & Twitter.pdf"]
    assert not more

    result = download(bucket, files, tmp_path, fetcher, max_total_bytes=10**6)
    assert (tmp_path / "freebies/TripleYourIncome.pdf").read_bytes() == PDF
    assert [s.reason for s in result.skipped] == [
        f"size doesn't match the list (9 vs {len(PDF)} bytes)"
    ]


def test_private_bucket_list_gives_a_clear_message() -> None:
    web = FakeWeb({"https://shut.s3.amazonaws.com/?list-type=2": (403, {}, b"<Error/>")})
    bucket = detect("https://shut.s3.amazonaws.com/")
    assert bucket is not None
    with pytest.raises(Refused, match="doesn't share its file list"):
        list_files(bucket, web.fetcher())


def test_lists_google_cloud_storage() -> None:
    listing = {
        "items": [
            {"name": "data/a.csv", "size": "4", "updated": "2024", "md5Hash": "AAAA"},
            {"name": "data/", "size": "0"},
        ]
    }
    url = "https://storage.googleapis.com/storage/v1/b/pub/o?prefix=data&maxResults=1000"
    web = FakeWeb({url: file(json.dumps(listing).encode(), "application/json")})
    bucket = detect("gs://pub/data")
    assert bucket is not None
    files, _ = list_files(bucket, web.fetcher())
    assert files == [
        BucketFile(
            "data/a.csv", 4, "2024", "000000", "https://storage.googleapis.com/pub/data/a.csv"
        )
    ]


def test_lists_azure_containers() -> None:
    md5 = base64.b64encode(hashlib.md5(PDF).digest()).decode()
    xml = (
        "<EnumerationResults><Blobs><Blob><Name>x y.pdf</Name><Properties>"
        f"<Last-Modified>Mon</Last-Modified><Content-Length>{len(PDF)}</Content-Length>"
        f"<Content-MD5>{md5}</Content-MD5></Properties></Blob></Blobs><NextMarker/>"
        "</EnumerationResults>"
    )
    endpoint = "https://acct.blob.core.windows.net/open"
    web = FakeWeb(
        {f"{endpoint}?restype=container&comp=list": file(xml.encode(), "application/xml")}
    )
    bucket = Bucket("Azure Blob Storage", "azure", endpoint, "open")
    files, _ = list_files(bucket, web.fetcher())
    assert files[0].md5 == PDF_MD5
    assert files[0].url == f"{endpoint}/x%20y.pdf"


def test_files_over_the_cap_are_skipped_before_downloading(tmp_path: Path) -> None:
    web = FakeWeb({})
    big = BucketFile("big.iso", 10**9, "", "", "https://b.s3.amazonaws.com/big.iso")
    bucket = Bucket("Amazon S3", "s3", "https://b.s3.amazonaws.com/", "b")
    result = download(bucket, [big], tmp_path, web.fetcher(), max_total_bytes=10**10)
    assert result.skipped[0].reason.startswith("too big")
    assert "https://b.s3.amazonaws.com/big.iso" not in web.urls()


def test_a_non_list_answer_is_refused_clearly() -> None:
    web = FakeWeb({"https://odd.s3.amazonaws.com/?list-type=2": file(b"<html>hi", "text/html")})
    bucket = detect("https://odd.s3.amazonaws.com/")
    assert bucket is not None
    with pytest.raises(Refused, match="wasn't a bucket file list"):
        list_files(bucket, web.fetcher())
