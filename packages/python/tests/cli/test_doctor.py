"""Unit tests for awaithumans doctor pre-flight checks.

One test per check, covering happy path + each failure mode.
Env vars mocked via monkeypatch; no real network or Slack API calls.
"""

from __future__ import annotations

import base64
import secrets
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from awaithumans.cli.commands.doctor import (
    _check_admin_api_token,
    _check_database,
    _check_discovery_file,
    _check_payload_key,
    _check_slack_pairing,
    _check_slack_public_url,
    _check_slack_token_shape,
)
from awaithumans.server.core.config import settings

# ── PAYLOAD_KEY ───────────────────────────────────────────────────────────────


def test_check_payload_key_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PAYLOAD_KEY", secrets.token_urlsafe(32))
    status, msg = _check_payload_key()
    assert status == "pass"
    assert "32 bytes" in msg


def test_check_payload_key_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PAYLOAD_KEY", None)
    status, msg = _check_payload_key()
    assert status == "fail"
    assert "not set" in msg


def test_check_payload_key_wrong_length(monkeypatch: pytest.MonkeyPatch) -> None:
    short = base64.urlsafe_b64encode(b"tooshort").decode()
    monkeypatch.setattr(settings, "PAYLOAD_KEY", short)
    status, msg = _check_payload_key()
    assert status == "fail"


# ── ADMIN_API_TOKEN ───────────────────────────────────────────────────────────


def test_check_admin_api_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "tok-abc123")
    status, _ = _check_admin_api_token()
    assert status == "pass"


def test_check_admin_api_token_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", None)
    status, msg = _check_admin_api_token()
    assert status == "warn"
    assert "503" in msg


# ── DATABASE ──────────────────────────────────────────────────────────────────


def test_check_database_sqlite_writable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "test.db"))
    status, msg = _check_database()
    assert status == "pass"
    assert "writable" in msg


def test_check_database_sqlite_not_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(0o444)
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "DB_PATH", str(ro_dir / "test.db"))
    try:
        status, msg = _check_database()
        assert status == "fail"
        assert "not writable" in msg
    finally:
        ro_dir.chmod(0o755)


def test_check_database_postgres_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:pass@localhost:9999/db")
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("Connection refused")
    with patch("sqlalchemy.create_engine", return_value=mock_engine):
        status, msg = _check_database()
    assert status == "fail"
    assert "unreachable" in msg


# ── SLACK TOKEN SHAPE ─────────────────────────────────────────────────────────


def test_check_slack_token_shape_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
    status, msg = _check_slack_token_shape()
    assert status == "pass"
    assert "skipping" in msg


def test_check_slack_token_shape_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-123456789")
    status, _ = _check_slack_token_shape()
    assert status == "pass"


def test_check_slack_token_shape_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxp-wrong-prefix")
    status, msg = _check_slack_token_shape()
    assert status == "warn"
    assert "malformed" in msg


# ── SLACK PAIRING ─────────────────────────────────────────────────────────────


def test_check_slack_pairing_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-123")
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "secret")
    status, msg = _check_slack_pairing()
    assert status == "pass"
    assert "both set" in msg


def test_check_slack_pairing_both_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", None)
    status, msg = _check_slack_pairing()
    assert status == "pass"
    assert "both unset" in msg


def test_check_slack_pairing_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-123")
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", None)
    status, msg = _check_slack_pairing()
    assert status == "warn"
    assert "SLACK_SIGNING_SECRET" in msg


def test_check_slack_pairing_secret_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "secret")
    status, msg = _check_slack_pairing()
    assert status == "warn"
    assert "SLACK_BOT_TOKEN" in msg


# ── SLACK PUBLIC URL ──────────────────────────────────────────────────────────


def test_check_slack_public_url_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", None)
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", None)
    status, msg = _check_slack_public_url()
    assert status == "pass"
    assert "skipping" in msg


def test_check_slack_public_url_localhost_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-123")
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setattr(settings, "PUBLIC_URL", "http://localhost:3001")
    status, msg = _check_slack_public_url()
    assert status == "warn"
    assert "localhost" in msg


def test_check_slack_public_url_public_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-123")
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://myapp.example.com")
    status, _ = _check_slack_public_url()
    assert status == "pass"


# ── DISCOVERY FILE ────────────────────────────────────────────────────────────


def test_check_discovery_file_not_found(tmp_path: Path) -> None:
    with patch.object(Path, "home", return_value=tmp_path):
        status, msg = _check_discovery_file()
    assert status == "pass"
    assert "not found" in msg


def test_check_discovery_file_writable(tmp_path: Path) -> None:
    (tmp_path / ".awaithumans-dev.json").write_text("{}", encoding="utf-8")
    with patch.object(Path, "home", return_value=tmp_path):
        status, msg = _check_discovery_file()
    assert status == "pass"
    assert "writable" in msg


def test_check_discovery_file_is_directory(tmp_path: Path) -> None:
    (tmp_path / ".awaithumans-dev.json").mkdir()
    with patch.object(Path, "home", return_value=tmp_path):
        status, msg = _check_discovery_file()
    assert status == "fail"
    assert "directory" in msg
