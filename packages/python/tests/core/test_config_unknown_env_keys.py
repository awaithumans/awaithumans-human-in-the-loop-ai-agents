"""Regression tests for the dual-namespace `.env` issue.

Before the fix, pydantic-settings' dotenv source enforced
`extra="forbid"` by default on unknown keys. Any `AWAITHUMANS_*` key
in a project's `.env` that wasn't a declared server field would crash
`Settings()` at module-import — including SDK-side vars like
`AWAITHUMANS_URL` that the SDK reads but the server has no field for.

These tests pin three things:
  1. Settings() instantiation does not crash when `.env` contains an
     unrecognized AWAITHUMANS_* key.
  2. `unknown_env_keys()` returns the unrecognized keys so the caller
     (`app.create_app`) can emit a startup warning.
  3. Known server keys are NOT in the unknown list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awaithumans.server.core.config import Settings, unknown_env_keys


def test_settings_does_not_crash_on_unknown_awaithumans_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug-was-here case: an SDK-side var in a shared .env."""
    env = tmp_path / ".env"
    env.write_text(
        "AWAITHUMANS_URL=http://localhost:3001\nAWAITHUMANS_EMAIL_TRANSPORT=smtp\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # Pydantic-settings reads `.env` from cwd by default.
    Settings()  # MUST not raise


def test_unknown_env_keys_reports_sdk_only_var(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "AWAITHUMANS_URL=http://localhost:3001\nAWAITHUMANS_EMAIL_TRANSPORT=smtp\n",
        encoding="utf-8",
    )
    unknown = unknown_env_keys(env_path=env)
    assert unknown == ["AWAITHUMANS_URL"], (
        "Expected AWAITHUMANS_URL flagged as unknown (SDK-side), "
        f"AWAITHUMANS_EMAIL_TRANSPORT silently accepted. Got: {unknown}"
    )


def test_unknown_env_keys_ignores_known_server_vars(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "AWAITHUMANS_PAYLOAD_KEY=abc123\n"
        "AWAITHUMANS_PUBLIC_URL=https://example.com\n"
        "AWAITHUMANS_EMAIL_TRANSPORT=smtp\n",
        encoding="utf-8",
    )
    assert unknown_env_keys(env_path=env) == []


def test_unknown_env_keys_handles_comments_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# top comment\n"
        "\n"
        "AWAITHUMANS_URL=http://x\n"
        "   # indented comment\n"
        "AWAITHUMANS_FAKE_VAR=oops\n",
        encoding="utf-8",
    )
    assert sorted(unknown_env_keys(env_path=env)) == [
        "AWAITHUMANS_FAKE_VAR",
        "AWAITHUMANS_URL",
    ]


def test_unknown_env_keys_ignores_non_awaithumans_keys(tmp_path: Path) -> None:
    """Other env vars (DATABASE_URL, PATH, etc.) are not our concern."""
    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=postgresql://...\nSOME_OTHER_KEY=value\n",
        encoding="utf-8",
    )
    assert unknown_env_keys(env_path=env) == []


def test_unknown_env_keys_returns_empty_when_no_env_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.env"
    assert unknown_env_keys(env_path=missing) == []


def test_unknown_env_keys_is_case_insensitive(tmp_path: Path) -> None:
    """`.env` lines can use lowercase; we normalize to uppercase."""
    env = tmp_path / ".env"
    env.write_text("awaithumans_url=http://x\n", encoding="utf-8")
    assert unknown_env_keys(env_path=env) == ["AWAITHUMANS_URL"]
