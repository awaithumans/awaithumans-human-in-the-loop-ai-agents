"""Start the awaithumans server + dashboard for local development."""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import secrets
import socket
from pathlib import Path

import typer

from awaithumans.utils.discovery import delete_discovery, write_discovery

logger = logging.getLogger("awaithumans.cli")


def _ensure_dev_public_url(port: int) -> None:
    """Default PUBLIC_URL to the actual loopback URL for dev.

    Mutates the already-loaded settings singleton AND sets the env
    var, matching the PAYLOAD_KEY fix pattern — settings reads env at
    import time, which already happened before this CLI code runs."""
    from awaithumans.server.core.config import settings

    if os.environ.get("AWAITHUMANS_PUBLIC_URL"):
        return  # Operator-supplied — respect it

    url = f"http://localhost:{port}"
    os.environ["AWAITHUMANS_PUBLIC_URL"] = url
    settings.PUBLIC_URL = url


def _ensure_dev_payload_key(db_path: str) -> None:
    """Generate a local PAYLOAD_KEY for dev if one isn't set in env.

    PAYLOAD_KEY is now always required — it signs session cookies and
    encrypts at-rest columns. For `awaithumans dev`, we don't want to
    force every first-time user to run `python -c 'import secrets...'`
    before the server starts.

    Cached in `<.awaithumans dir>/payload.key` so sessions survive
    restarts. File is 0600. Production must still set the env var
    explicitly (this function only writes when env is unset).

    Also mutates the already-loaded `settings` singleton — the Settings
    object was instantiated at import time, before this function ran,
    so setting only the env var wouldn't feed back into `settings.PAYLOAD_KEY`.
    """
    from awaithumans.server.core import encryption
    from awaithumans.server.core.config import settings

    if os.environ.get("AWAITHUMANS_PAYLOAD_KEY") or settings.PAYLOAD_KEY:
        return

    key_path = Path(db_path).parent / "payload.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        key = key_path.read_text().strip()
    else:
        key = secrets.token_urlsafe(32)
        key_path.write_text(key)
        # Windows or exotic filesystems — best-effort.
        with contextlib.suppress(OSError):
            key_path.chmod(0o600)
        logger.info("Generated dev PAYLOAD_KEY at %s (0600)", key_path)

    os.environ["AWAITHUMANS_PAYLOAD_KEY"] = key
    settings.PAYLOAD_KEY = key
    encryption.reset_key_cache()


def _ensure_dev_admin_token(db_path: str) -> str:
    """Generate a local ADMIN_API_TOKEN for dev if one isn't set in env.

    The SDK reads this (via the discovery file or env var) and sends
    it as `Authorization: Bearer <token>` on every request. Without
    it, `await_human()` hits the auth middleware and 401s — which is
    the correct behavior in prod, but a DX wall in dev.

    Cached in `<.awaithumans dir>/admin.token` so the token is stable
    across restarts — agents hold onto it between runs, and the dev
    workflow stays zero-config.

    Returns the token so the caller can put it in the discovery file
    (SDK auto-pickup) in the same pass.
    """
    from awaithumans.server.core.config import settings

    if os.environ.get("AWAITHUMANS_ADMIN_API_TOKEN") or settings.ADMIN_API_TOKEN:
        return settings.ADMIN_API_TOKEN or os.environ["AWAITHUMANS_ADMIN_API_TOKEN"]

    token_path = Path(db_path).parent / "admin.token"
    token_path.parent.mkdir(parents=True, exist_ok=True)

    if token_path.exists():
        token = token_path.read_text().strip()
    else:
        token = secrets.token_urlsafe(32)
        token_path.write_text(token)
        with contextlib.suppress(OSError):
            token_path.chmod(0o600)
        logger.info("Generated dev ADMIN_API_TOKEN at %s (0600)", token_path)

    os.environ["AWAITHUMANS_ADMIN_API_TOKEN"] = token
    settings.ADMIN_API_TOKEN = token
    return token


def _is_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


def _find_available_port(host: str, preferred: int, max_attempts: int = 10) -> int:
    """Find an available port, starting from the preferred one.

    Tries the preferred port first, then increments until an available one is found.
    """
    for offset in range(max_attempts):
        candidate = preferred + offset
        if _is_port_available(host, candidate):
            return candidate

    raise typer.Exit(
        code=1,
    )


def dev(
    host: str = typer.Option("0.0.0.0", help="Host to bind to."),
    port: int = typer.Option(3001, help="Port for the API server."),
    db_path: str = typer.Option(".awaithumans/dev.db", help="SQLite database path."),
    log_level: str = typer.Option("INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)."),
) -> None:
    """Start the awaithumans server + dashboard for local development."""
    import uvicorn

    from awaithumans.server.core.logging_config import setup_logging

    setup_logging(log_level)

    # Find an available port
    actual_port = _find_available_port(host, port)
    if actual_port != port:
        logger.info(
            "Port %d is in use — using port %d instead",
            port,
            actual_port,
        )

    # Set config via env vars so Settings picks them up
    os.environ.setdefault("AWAITHUMANS_DB_PATH", db_path)
    os.environ.setdefault("AWAITHUMANS_LOG_LEVEL", log_level)
    os.environ.setdefault("AWAITHUMANS_HOST", host)
    os.environ.setdefault("AWAITHUMANS_PORT", str(actual_port))

    # PUBLIC_URL drives the setup banner, Slack OAuth callbacks, email
    # magic links, and everything else that needs an externally-visible
    # URL. If the operator didn't set it explicitly, default to
    # `http://localhost:<actual_port>` — not the bind host, which is
    # often 0.0.0.0 and not usable in a browser.
    _ensure_dev_public_url(actual_port)

    # Ensure a PAYLOAD_KEY exists for local dev (cached in .awaithumans/)
    _ensure_dev_payload_key(db_path)

    # Ensure an ADMIN_API_TOKEN exists so agents can reach the authed
    # /api/tasks endpoints without the operator configuring anything.
    admin_token = _ensure_dev_admin_token(db_path)

    # Write discovery file so SDKs and the dashboard can auto-find us.
    # The admin token rides along so the Python SDK picks it up
    # automatically — agents on the same machine need zero config.
    write_discovery(host=host, port=actual_port, admin_token=admin_token)
    atexit.register(delete_discovery)

    logger.info("Starting awaithumans server on http://%s:%d", host, actual_port)
    logger.info("Dashboard at http://%s:%d", host, actual_port)
    logger.info("SQLite database at %s", db_path)
    # Tell the user where the admin token is + that they don't have to
    # do anything with it. The Python and TypeScript SDKs both read
    # it from the discovery file automatically, so a fresh shell
    # running `python refund.py` or `npm start` "just works." The
    # only time an operator needs to copy this is when they're
    # talking to the API from curl.
    logger.info(
        "Admin token written to %s (SDKs auto-detect via "
        "~/.awaithumans-dev.json — copy only for curl)",
        Path(db_path).parent / "admin.token",
    )
    logger.info("Ready — waiting for tasks...")

    from awaithumans.server.app import create_app

    application = create_app(serve_dashboard=True)
    try:
        uvicorn.run(application, host=host, port=actual_port, log_level=log_level.lower())
    finally:
        delete_discovery()
