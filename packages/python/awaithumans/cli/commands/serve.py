"""Production entrypoint — runs uvicorn against the app factory.

This is the Docker / Kubernetes / Container Apps entrypoint. It differs
from ``awaithumans dev`` in three important ways:

1. **No dev affordances.** No SQLite path defaulting, no auto-generated
   PAYLOAD_KEY, no auto-generated admin token, no discovery file. If
   the operator hasn't set what's needed, the server refuses to start
   (PAYLOAD_KEY) or this command refuses (DATABASE_URL / DB_PATH).
2. **Explicit storage choice required.** Refuses to start if neither
   ``AWAITHUMANS_DATABASE_URL`` nor ``AWAITHUMANS_DB_PATH`` is set.
   The dev command silently falls back to ``.awaithumans/dev.db``
   inside the container, which is ephemeral on Azure Container Apps
   and a few other runtimes — operators lose all task data on every
   restart and the only signal is a confused team.
3. **No misleading log lines.** ``awaithumans dev`` prints
   ``"SQLite database at .awaithumans/dev.db"`` even when
   ``AWAITHUMANS_DATABASE_URL`` is set, because it was written for a
   single use case and grew the prod path bolt-on. ``serve`` only
   logs what's actually true.

Defaults match the Dockerfile env: host ``0.0.0.0``, port ``3001``,
log level ``info``. Override via env vars or CLI flags.
"""

from __future__ import annotations

import logging
import os

import typer

logger = logging.getLogger("awaithumans.cli.serve")


_MISSING_STORAGE_MESSAGE = (
    "awaithumans serve: refusing to start — neither AWAITHUMANS_DATABASE_URL\n"
    "nor AWAITHUMANS_DB_PATH is set.\n"
    "\n"
    "Production deployments must explicitly choose their data store. Pick one:\n"
    "\n"
    "  Postgres (recommended for any multi-instance or durable deployment):\n"
    "    AWAITHUMANS_DATABASE_URL=postgresql://user:pw@host:5432/dbname\n"
    "\n"
    "  SQLite on a mounted, persistent volume:\n"
    "    AWAITHUMANS_DB_PATH=/var/lib/awaithumans/awaithumans.db\n"
    "\n"
    "For local development that auto-provisions SQLite, use `awaithumans dev`\n"
    "instead.\n"
)


def serve(
    host: str = typer.Option(
        "0.0.0.0",
        help="Host interface to bind. Overrides AWAITHUMANS_HOST.",
    ),
    port: int = typer.Option(
        3001,
        help="Port to bind. Overrides AWAITHUMANS_PORT.",
    ),
    log_level: str = typer.Option(
        "info",
        help="Uvicorn log level (debug, info, warning, error).",
    ),
) -> None:
    """Run the awaithumans server in production mode.

    Unlike ``awaithumans dev``, this command does not auto-provision
    keys, tokens, or a SQLite path. Set ``AWAITHUMANS_PAYLOAD_KEY``
    and either ``AWAITHUMANS_DATABASE_URL`` or ``AWAITHUMANS_DB_PATH``
    explicitly before running.
    """
    import uvicorn

    # Hard refusal: production must explicitly pick a data store.
    # We check raw env vars rather than settings.DATABASE_URL because
    # settings.DB_PATH always has a non-empty default value, so we
    # can't distinguish "operator chose it" from "Settings filled it in".
    db_url = (os.environ.get("AWAITHUMANS_DATABASE_URL") or "").strip()
    db_path = (os.environ.get("AWAITHUMANS_DB_PATH") or "").strip()
    if not db_url and not db_path:
        typer.echo(_MISSING_STORAGE_MESSAGE, err=True)
        raise typer.Exit(code=1)

    # Propagate CLI overrides into env so Settings (already imported
    # by FastAPI route modules at module load time) sees them when
    # create_app() runs. setdefault preserves operator-supplied env.
    os.environ.setdefault("AWAITHUMANS_HOST", host)
    os.environ.setdefault("AWAITHUMANS_PORT", str(port))
    os.environ.setdefault("AWAITHUMANS_LOG_LEVEL", log_level.upper())

    # Importing here (not at module level) keeps `awaithumans --help`
    # and `awaithumans version` fast for the lightweight-SDK install
    # path. The server modules pull in FastAPI, SQLModel, etc., which
    # add ~300ms of import time we don't want on every CLI invocation.
    from awaithumans.server.app import create_app

    logger.info(
        "Starting awaithumans server in production mode on http://%s:%d (database=%s)",
        host,
        port,
        "postgres" if db_url else f"sqlite ({db_path})",
    )

    application = create_app(serve_dashboard=True)
    uvicorn.run(application, host=host, port=port, log_level=log_level.lower())
