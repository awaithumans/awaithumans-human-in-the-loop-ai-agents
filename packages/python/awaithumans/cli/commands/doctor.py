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


def doctor() -> None:
    """Run pre-flight checks against the current environment."""
    typer.echo("awaithumans doctor — checking environment")
    typer.echo("─" * 45)
    typer.echo()

    checks = [_check_payload_key, _check_admin_api_token, _check_database]
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