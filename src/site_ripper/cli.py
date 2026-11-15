"""Command line: ``site-ripper <link>``."""

import argparse
import sys
from pathlib import Path

from site_ripper.buckets import Bucket, NotListable, detect, download, list_files
from site_ripper.crawler import MB, Event, Limits, crawl
from site_ripper.fetcher import Fetcher, Refused
from site_ripper.packager import finish, new_output_dir


def main(argv: list[str] | None = None) -> int:
    """Run the command line tool and return its exit code."""
    args = _parser().parse_args(argv)
    if not args.yes and not _confirm():
        print("Stopped. Only copy sites and buckets you have the right to copy.")
        return 1
    limits = Limits(
        max_pages=args.max_pages,
        max_depth=args.depth,
        max_total_bytes=args.max_mb * MB,
        max_file_bytes=args.max_file_mb * MB,
        whole_host=args.whole_host,
    ).clamped()
    fetcher = Fetcher(max_file_bytes=limits.max_file_bytes)
    try:
        bucket = detect(args.url, force_s3=args.s3)
        if bucket:
            return _bucket(bucket, args, fetcher, limits)
        out_dir = new_output_dir(args.out, _host(args.url))
        result = crawl(args.url, out_dir, fetcher, limits, on_event=_print_event)
    except (NotListable, Refused) as exc:
        print(f"Can't do that: {exc}", file=sys.stderr)
        return 2
    finally:
        fetcher.close()
    print(f"\nDone ({result.stop_reason}). Saved {len(result.saved)} files.")
    print(f"Folder: {out_dir}\nZip:    {finish(result)}")
    return 0


def _bucket(bucket: Bucket, args: argparse.Namespace, fetcher: Fetcher, limits: Limits) -> int:
    files, more = list_files(bucket, fetcher)
    print(f"{bucket.provider} bucket '{bucket.name}': {len(files)} files")
    for item in files:
        print(f"  {item.size / MB:9.2f} MB  {item.key}")
    if more:
        print(f"  ...listing stopped at {len(files)} files")
    chosen = files if args.all else [f for f in files if f.key in set(args.file)]
    if not chosen:
        print("\nTo download, add --all or one or more --file NAME.")
        return 0
    out_dir = new_output_dir(args.out, bucket.name)
    result = download(bucket, chosen, out_dir, fetcher, limits.max_total_bytes, _print_event)
    print(f"\nDone ({result.stop_reason}). Saved {len(result.saved)} files.")
    print(f"Folder: {out_dir}\nZip:    {finish(result)}")
    return 0


def _print_event(event: Event) -> None:
    if event["type"] == "saved":
        print(f"saved    {event['url']}")
    elif event["type"] == "skipped":
        print(f"skipped  {event['url']}  ({event['reason']})")


def _confirm() -> bool:
    try:
        answer = input(
            "Do you have the right to copy this (your own, openly licensed, or "
            "with permission)? [y/N] "
        )
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0] or "site"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="site-ripper",
        description="Save a polite offline copy of a website, or files from a public bucket.",
    )
    p.add_argument("url", help="web page or bucket link to copy")
    p.add_argument("--max-pages", type=int, default=200, help="pages to save (max 1000)")
    p.add_argument("--depth", type=int, default=3, help="clicks away from the start page")
    p.add_argument("--whole-host", action="store_true", help="don't stay inside the start folder")
    p.add_argument("--max-mb", type=int, default=500, help="total size cap in MB (max 2048)")
    p.add_argument("--max-file-mb", type=int, default=25, help="per-file size cap in MB")
    p.add_argument("--s3", action="store_true", help="treat the link as an S3-compatible bucket")
    p.add_argument("--all", action="store_true", help="bucket: download every listed file")
    p.add_argument("--file", action="append", default=[], help="bucket: download this file")
    p.add_argument("--out", type=Path, default=Path("output"), help="where to save")
    p.add_argument("--yes", action="store_true", help="confirm you have the right to copy it")
    return p


if __name__ == "__main__":
    sys.exit(main())
