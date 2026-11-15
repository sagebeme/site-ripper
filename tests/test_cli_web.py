from pathlib import Path

import pytest

from site_ripper.cli import main
from site_ripper.web import create_app


def test_cli_refuses_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["https://example.com/"]) == 1


def test_cli_explains_unlistable_buckets(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["https://pub-abc.r2.dev/", "--yes"]) == 2
    assert "Cloudflare R2" in capsys.readouterr().err


def test_web_page_loads(tmp_path: Path) -> None:
    client = create_app(tmp_path).test_client()
    page = client.get("/", base_url="http://127.0.0.1:5000")
    assert page.status_code == 200
    assert b"I have the right to copy this" in page.data


def test_web_requires_the_right_to_copy(tmp_path: Path) -> None:
    client = create_app(tmp_path).test_client()
    got = client.post(
        "/api/start", json={"url": "https://example.com/"}, base_url="http://127.0.0.1:5000"
    )
    assert got.status_code == 400
    assert "right to copy" in got.get_json()["error"]


def test_web_rejects_other_host_names(tmp_path: Path) -> None:
    client = create_app(tmp_path).test_client()
    assert client.get("/", base_url="http://evil.example:5000").status_code == 403


def test_web_stop_needs_json(tmp_path: Path) -> None:
    client = create_app(tmp_path).test_client()
    form_post = client.post("/api/stop", data="x", base_url="http://127.0.0.1:5000")
    assert form_post.status_code == 415
