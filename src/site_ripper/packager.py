"""What a finished run produced: the folder, REPORT.md, manifest.json and the zip."""

import json
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from site_ripper.paths import safe_segment

_REPORT_LIST_LIMIT = 300


@dataclass
class Entry:
    """One saved file."""

    url: str
    path: str
    """Relative to the run's output folder."""
    content_type: str
    size: int


@dataclass
class Skip:
    """One URL that was not saved, and why."""

    url: str
    reason: str


@dataclass
class Result:
    """Everything a crawl or bucket download did."""

    source: str
    out_dir: Path
    saved: list[Entry] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stop_reason: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.saved)


def new_output_dir(root: Path, name: str) -> Path:
    """Create and return a fresh folder like ``output/example.com-2026-11-16T1430``."""
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M")
    base = root / f"{safe_segment(name)}-{stamp}"
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}-{n}")
    folder.mkdir(parents=True)
    return folder


def finish(result: Result) -> Path:
    """Write manifest.json and REPORT.md, then zip the folder.

    Returns:
        The path of the zip, next to the output folder.
    """
    manifest = {
        "source": result.source,
        "stop_reason": result.stop_reason,
        "saved": [asdict(e) for e in result.saved],
        "skipped": [asdict(s) for s in result.skipped],
        "warnings": result.warnings,
    }
    (result.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (result.out_dir / "REPORT.md").write_text(_report(result))

    zip_path = result.out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(result.out_dir.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(result.out_dir.parent))
    return zip_path


def _report(result: Result) -> str:
    lines = [
        f"# Offline copy of {result.source}",
        "",
        f"- Saved: **{len(result.saved)} files** ({result.total_bytes / 1_048_576:.1f} MB)",
        f"- Skipped: **{len(result.skipped)}**",
        f"- Stopped because: {result.stop_reason}",
        "",
        "Only keep or share this copy if you have the right to.",
        "",
    ]
    if result.warnings:
        lines += ["## Warnings", "", *(f"- {w}" for w in result.warnings), ""]
    lines += ["## Saved", "", "| File | From |", "|---|---|"]
    lines += [f"| `{e.path}` | {e.url} |" for e in result.saved[:_REPORT_LIST_LIMIT]]
    lines += _more(len(result.saved))
    lines += ["", "## Skipped", "", "| URL | Why |", "|---|---|"]
    lines += [f"| {s.url} | {s.reason} |" for s in result.skipped[:_REPORT_LIST_LIMIT]]
    lines += _more(len(result.skipped))
    return "\n".join(lines) + "\n"


def _more(count: int) -> list[str]:
    extra = count - _REPORT_LIST_LIMIT
    return [f"| …and {extra} more (see manifest.json) | |"] if extra > 0 else []
