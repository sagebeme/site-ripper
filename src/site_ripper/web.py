"""The local web page: paste a link, watch progress, download the zip.

It only listens on 127.0.0.1 (this computer). One job runs at a time.
"""

import json
import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from site_ripper.buckets import Bucket, BucketFile, NotListable, detect, download, list_files
from site_ripper.crawler import MB, Event, Limits, crawl
from site_ripper.fetcher import Fetcher, Refused
from site_ripper.packager import Result, finish, new_output_dir

HOST, PORT = "127.0.0.1", 5000
log = logging.getLogger(__name__)


class Job:
    """One crawl or bucket download, plus the progress messages it has sent."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.changed = threading.Condition()
        self.stop = threading.Event()
        self.running = False
        self.zip_path: Path | None = None
        self.bucket: Bucket | None = None
        self.files: list[BucketFile] = []
        self.limits = Limits()

    def emit(self, event: Event) -> None:
        with self.changed:
            self.events.append(event)
            self.changed.notify_all()

    def start(self, work: Callable[[], Result]) -> None:
        """Run ``work`` in the background, then package what it saved."""
        self.running, self.stop, self.events = True, threading.Event(), []

        def run() -> None:
            try:
                result = work()
                self.zip_path = finish(result)
                self.emit(
                    {
                        "type": "finished",
                        "reason": result.stop_reason,
                        "saved": len(result.saved),
                        "skipped": len(result.skipped),
                        "megabytes": round(result.total_bytes / MB, 2),
                        "warnings": result.warnings[:20],
                    }
                )
            except Exception as exc:  # report any crash to the page instead of hanging it
                log.exception("job failed")
                self.emit({"type": "error", "message": f"Something went wrong: {exc}"})
            finally:
                self.running = False

        threading.Thread(target=run, daemon=True).start()


def create_app(output_root: Path = Path("output")) -> Flask:
    """Build the Flask app. ``output_root`` is where copies are saved."""
    app = Flask(__name__)
    job = Job()
    # Flask reads relative file paths from its own code folder, so make it absolute.
    output_root = output_root.resolve()

    @app.before_request
    def only_local() -> None:
        # Stops other websites reaching this page through DNS tricks.
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/api/start")
    def start() -> tuple[Response, int]:
        data: dict[str, Any] = request.get_json()
        if not data.get("agree"):
            return _error("Please confirm you have the right to copy this.")
        if job.running:
            return _error("A copy is already running. Stop it first.", 409)
        url = str(data.get("url", "")).strip()
        if "://" not in url:
            url = f"https://{url}"
        job.limits = Limits(
            max_pages=int(data.get("max_pages", 200)),
            max_depth=int(data.get("depth", 3)),
            whole_host=bool(data.get("whole_host")),
        ).clamped()
        try:
            bucket = detect(url, force_s3=bool(data.get("s3")))
            if bucket:
                return _list_bucket(bucket)
        except NotListable as exc:
            return _error(str(exc))
        out_dir = new_output_dir(output_root, url.split("://", 1)[1].split("/", 1)[0])

        def work() -> Result:
            fetcher = Fetcher(max_file_bytes=job.limits.max_file_bytes)
            try:
                return crawl(url, out_dir, fetcher, job.limits, job.emit, job.stop)
            finally:
                fetcher.close()

        job.start(work)
        return jsonify({"mode": "crawl"}), 200

    def _list_bucket(bucket: Bucket) -> tuple[Response, int]:
        fetcher = Fetcher()
        try:
            files, more = list_files(bucket, fetcher)
        except Refused as exc:
            return _error(f"Can't list this bucket: {exc}")
        finally:
            fetcher.close()
        job.bucket, job.files = bucket, files
        rows = [{"key": f.key, "size": f.size, "modified": f.modified} for f in files]
        body = {"mode": "bucket", "provider": bucket.provider, "name": bucket.name}
        return jsonify({**body, "files": rows, "more": more}), 200

    @app.post("/api/bucket/download")
    def bucket_download() -> tuple[Response, int]:
        keys = set(request.get_json().get("keys", []))
        bucket = job.bucket
        if bucket is None or job.running:
            return _error("List a bucket first, and wait for any running copy to finish.")
        chosen = [f for f in job.files if f.key in keys]
        if not chosen:
            return _error("Tick at least one file.")
        out_dir = new_output_dir(output_root, bucket.name)

        def work() -> Result:
            fetcher = Fetcher(max_file_bytes=job.limits.max_file_bytes)
            try:
                total = job.limits.max_total_bytes
                return download(bucket, chosen, out_dir, fetcher, total, job.emit, job.stop)
            finally:
                fetcher.close()

        job.start(work)
        return jsonify({"mode": "download", "count": len(chosen)}), 200

    @app.post("/api/stop")
    def stop() -> tuple[Response, int]:
        request.get_json()  # JSON-only, so other sites can't press Stop for you
        job.stop.set()
        return jsonify({"ok": True}), 200

    @app.get("/api/events")
    def events() -> Response:
        """Stream progress to the page as Server-Sent Events."""

        def stream() -> Iterator[str]:
            sent = 0
            while True:
                with job.changed:
                    if len(job.events) <= sent:
                        job.changed.wait(timeout=15)
                    new = job.events[sent:]
                sent += len(new)
                if not new:
                    yield ": still here\n\n"  # keeps the connection open
                for event in new:
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] in ("finished", "error"):
                        return

        return Response(stream(), mimetype="text/event-stream")

    @app.get("/api/zip")
    def zip_file() -> Response:
        if job.zip_path is None or job.running:
            abort(404)
        return send_file(job.zip_path, as_attachment=True)

    return app


def _error(message: str, status: int = 400) -> tuple[Response, int]:
    return jsonify({"error": message}), status


def main() -> None:
    """Start the local web page at http://127.0.0.1:5000."""
    print(f"Open http://{HOST}:{PORT} in your browser. Press Ctrl+C to stop.")
    create_app().run(host=HOST, port=PORT, threaded=True)
