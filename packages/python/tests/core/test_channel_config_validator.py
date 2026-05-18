"""`validate_channel_config` warns about half-configured channels at boot.

Operators who set `EMAIL_TRANSPORT=smtp` but forget the matching
credentials used to find out only when the first send silently dropped
(now surfaced via `notification_failed` audit + banner). This boot-time
check catches the misconfig before any task runs.

These tests run the validator against a constructed `Settings` and
assert WARNING messages via a direct handler attached to the
validator's logger. We avoid pytest's `caplog` here because
`server.core.logging_config.setup_logging` calls
`root_logger.handlers.clear()` — once any other test in the suite
triggers `create_app()`, caplog's root-attached handler is gone and
all subsequent caplog captures return empty.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest

from awaithumans.server.core.channel_config_validator import (
    validate_channel_config,
)
from awaithumans.server.core.config import Settings


@pytest.fixture
def captured() -> Iterator[list[logging.LogRecord]]:
    """Capture WARNING+ records from the channel_config logger directly.

    Bypasses pytest's caplog so this test file works even after
    another test in the suite has called `setup_logging()` (which
    nukes root handlers, including caplog's).
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    logger_obj = logging.getLogger("awaithumans.server.core.channel_config")
    original_level = logger_obj.level
    original_propagate = logger_obj.propagate
    original_disabled = logger_obj.disabled
    logger_obj.addHandler(handler)
    logger_obj.setLevel(logging.WARNING)
    logger_obj.propagate = False  # don't double-emit to root
    # `disabled=True` is left set by pytest's caplog teardown in some
    # test orderings (it disables every logger to flush captures).
    # Without this reset, our captured handler never sees records.
    logger_obj.disabled = False
    try:
        yield records
    finally:
        logger_obj.removeHandler(handler)
        logger_obj.setLevel(original_level)
        logger_obj.propagate = original_propagate
        logger_obj.disabled = original_disabled


def _text(records: list[logging.LogRecord]) -> str:
    # `getMessage()` already does the `msg % args` formatting — don't
    # double-format, that raises "not all arguments converted" on any
    # record whose args don't match a new format pass.
    return "\n".join(r.getMessage() for r in records)


@pytest.fixture(autouse=True)
def _clean_awaithumans_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nuke any AWAITHUMANS_* env var leaked from another test's monkeypatch
    or from the developer's actual shell, so `Settings(...)` constructs
    only from the explicit kwargs we pass."""
    for key in list(os.environ.keys()):
        if key.startswith("AWAITHUMANS_"):
            monkeypatch.delenv(key, raising=False)


def _settings(**overrides: object) -> Settings:
    """Build a Settings with sensible test defaults overridden as needed.

    Avoids reading the real environment / .env file by passing the
    Settings constructor with explicit values.
    """
    base = {
        "EMAIL_TRANSPORT": None,
        "EMAIL_FROM": None,
        "SMTP_HOST": None,
        "SMTP_USER": None,
        "SMTP_PASSWORD": None,
        "RESEND_KEY": None,
        "SLACK_BOT_TOKEN": None,
        "SLACK_SIGNING_SECRET": None,
        "SLACK_CLIENT_ID": None,
        "SLACK_CLIENT_SECRET": None,
        "SLACK_INSTALL_TOKEN": None,
    }
    base.update(overrides)
    return Settings(**base)


# ─── Email — SMTP ───────────────────────────────────────────────────


def test_smtp_transport_warns_when_host_missing(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(
        _settings(
            EMAIL_TRANSPORT="smtp",
            SMTP_USER="user",
            SMTP_PASSWORD="pw",
            EMAIL_FROM="a@b.com",
        )
    )
    assert "AWAITHUMANS_SMTP_HOST" in _text(captured)
    assert "EMAIL_TRANSPORT=smtp" in _text(captured)


def test_smtp_transport_warns_when_from_missing(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(
        _settings(
            EMAIL_TRANSPORT="smtp",
            SMTP_HOST="smtp.example.com",
            SMTP_USER="user",
            SMTP_PASSWORD="pw",
            EMAIL_FROM=None,
        )
    )
    assert "AWAITHUMANS_EMAIL_FROM" in _text(captured)


def test_smtp_transport_fully_configured_emits_no_warning(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(
        _settings(
            EMAIL_TRANSPORT="smtp",
            SMTP_HOST="smtp.example.com",
            SMTP_USER="user",
            SMTP_PASSWORD="pw",
            EMAIL_FROM="a@b.com",
        )
    )
    assert _text(captured) == ""


# ─── Email — Resend ─────────────────────────────────────────────────


def test_resend_transport_warns_when_key_missing(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(_settings(EMAIL_TRANSPORT="resend", EMAIL_FROM="a@b.com"))
    assert "AWAITHUMANS_RESEND_KEY" in _text(captured)


def test_resend_fully_configured_emits_no_warning(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(
        _settings(EMAIL_TRANSPORT="resend", RESEND_KEY="re_xxx", EMAIL_FROM="a@b.com")
    )
    assert _text(captured) == ""


# ─── Email — unknown transport name ─────────────────────────────────


def test_unknown_email_transport_emits_warning(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(_settings(EMAIL_TRANSPORT="mailgun"))
    assert "mailgun" in _text(captured)
    assert "not a recognized transport" in _text(captured)


# ─── Slack — single-workspace ───────────────────────────────────────


def test_slack_bot_token_without_signing_secret_warns(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(_settings(SLACK_BOT_TOKEN="xoxb-test"))
    assert "AWAITHUMANS_SLACK_SIGNING_SECRET" in _text(captured)
    assert "inbound interactions" in _text(captured)


def test_slack_bot_token_with_signing_secret_no_warning(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(_settings(SLACK_BOT_TOKEN="xoxb-test", SLACK_SIGNING_SECRET="signsec"))
    assert _text(captured) == ""


# ─── Slack — OAuth multi-workspace ──────────────────────────────────


def test_slack_oauth_partial_config_warns_about_missing_pieces(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(_settings(SLACK_CLIENT_ID="123.456"))
    assert "AWAITHUMANS_SLACK_CLIENT_SECRET" in _text(captured)
    assert "AWAITHUMANS_SLACK_SIGNING_SECRET" in _text(captured)
    assert "AWAITHUMANS_SLACK_INSTALL_TOKEN" in _text(captured)


def test_slack_oauth_fully_configured_no_warning(
    captured: list[logging.LogRecord],
) -> None:
    validate_channel_config(
        _settings(
            SLACK_CLIENT_ID="123.456",
            SLACK_CLIENT_SECRET="secret",
            SLACK_SIGNING_SECRET="signsec",
            SLACK_INSTALL_TOKEN="install_xxx",
        )
    )
    assert _text(captured) == ""


# ─── Nothing configured ─────────────────────────────────────────────


def test_no_channels_configured_emits_no_warning(
    captured: list[logging.LogRecord],
) -> None:
    """A server with no notification channels at all is a valid setup
    (dashboard-only review). The validator must stay quiet."""
    validate_channel_config(_settings())
    assert _text(captured) == ""
