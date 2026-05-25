"""Pre-flight checks for common misconfigurations."""

from __future__ import annotations

import typer

from awaithumans.server.core.config import settings
from awaithumans.server.core.encryption import (
    EncryptionKeyError,
    EncryptionNotConfiguredError,
    get_key,
    reset_key_cache,
)


def _fmt(status: str, message: str) -> str:
    icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}[status]
    return f"{icon} {message}"


def _check_payload_key() -> tuple[str, str]:
    reset_key_cache()
    try:
        key = get_key()
        raw = settings.PAYLOAD_KEY or ""
        return ("pass", f"AWAITHUMANS_PAYLOAD_KEY set ({len(raw)} chars, decodes to {len(key)} bytes)")
    except EncryptionNotConfiguredError:
        return ("fail", "AWAITHUMANS_PAYLOAD_KEY not set — generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'")
    except EncryptionKeyError as e:
        return ("fail", f"AWAITHUMANS_PAYLOAD_KEY invalid: {e}")


def _check_admin_api_token() -> tuple[str, str]:
    if settings.ADMIN_API_TOKEN:
        return ("pass", "AWAITHUMANS_ADMIN_API_TOKEN set")
    return ("warn", "AWAITHUMANS_ADMIN_API_TOKEN not set — admin endpoints will return 503")


def _check_database() -> tuple[str, str]:
    from pathlib import Path

    url = settings.database_url_sync
    if "sqlite" in url:
        path_str = url.split("sqlite:///")[-1]
        db_dir = Path(path_str).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        try:
            test_file = db_dir / ".awaithumans_doctor_test"
            test_file.touch()
            test_file.unlink()
            return ("pass", f"Database at {url} is writable")
        except OSError as e:
            return ("fail", f"Database directory not writable: {e}")
    else:
        try:
            import sqlalchemy as sa
            engine = sa.create_engine(url, connect_args={"connect_timeout": 5})
            with engine.connect():
                pass
            return ("pass", "Database connection successful")
        except Exception as e:
            return ("fail", f"DATABASE_URL unreachable: {e}")
def _check_slack_token_shape() -> tuple[str, str]:
    token = settings.SLACK_BOT_TOKEN
    if not token:
        return ("pass", "Slack: SLACK_BOT_TOKEN not configured (skipping)")
    if not token.startswith("xoxb-"):
        return ("warn", "Slack: SLACK_BOT_TOKEN looks malformed — expected to start with 'xoxb-'")
    return ("pass", "Slack: SLACK_BOT_TOKEN shape looks correct")


def _check_slack_pairing() -> tuple[str, str]:
    has_token = bool(settings.SLACK_BOT_TOKEN)
    has_secret = bool(settings.SLACK_SIGNING_SECRET)
    if has_token == has_secret:
        return ("pass", "Slack: token + signing secret both set" if has_token else "Slack: token + signing secret both unset (skipping)")
    missing = "SLACK_SIGNING_SECRET" if has_token else "SLACK_BOT_TOKEN"
    return ("warn", f"Slack: {missing} is missing — set both or neither")


def _check_slack_public_url() -> tuple[str, str]:
    slack_configured = bool(settings.SLACK_BOT_TOKEN or settings.SLACK_SIGNING_SECRET)
    if not slack_configured:
        return ("pass", "Slack: not configured (skipping PUBLIC_URL check)")
    if "localhost" in settings.PUBLIC_URL or "127.0.0.1" in settings.PUBLIC_URL:
        return ("warn", f"Slack: PUBLIC_URL is {settings.PUBLIC_URL} — Slack interactivity buttons won't work from Slack's cloud. Use ngrok or Cloudflare Tunnel, or enable Socket Mode.")
    return ("pass", f"Slack: PUBLIC_URL is {settings.PUBLIC_URL}")


def doctor() -> None:
    """Run pre-flight checks against the current environment."""
    typer.echo("awaithumans doctor — checking environment")
    typer.echo("─" * 45)
    typer.echo()

    checks = [
        _check_payload_key,
        _check_admin_api_token,
        _check_database,
        _check_slack_token_shape,
        _check_slack_pairing,
        _check_slack_public_url,
    ]
    results = [check() for check in checks]

    for status, message in results:
        typer.echo(_fmt(status, message))

    typer.echo()
    passed = sum(1 for s, _ in results if s == "pass")
    warned  = sum(1 for s, _ in results if s == "warn")
    failed  = sum(1 for s, _ in results if s == "fail")
    typer.echo(f"{passed} checks passed, {warned} warnings, {failed} errors.")
    typer.echo()

    if failed:
        raise typer.Exit(code=1)