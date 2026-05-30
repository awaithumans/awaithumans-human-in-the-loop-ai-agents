"""Boot-time guard against ephemeral SQLite in production.

A production deployment that lands on the default SQLite path loses
all task data on every container restart in most runtimes — Azure
Container Apps, plain Docker without ``-v``, k8s without a PVC. The
Dockerfile's ``VOLUME`` directive is meant to prevent this but isn't
honored everywhere. The fix in ``server/app.py`` logs a loud
multi-line WARNING when production is detected with SQLite, unless
``AWAITHUMANS_ALLOW_EPHEMERAL_DB=true`` acknowledges the risk.

These tests pin the four corners of the truth table:

| ENVIRONMENT | DB         | ALLOW_EPHEMERAL_DB | WARN? |
|-------------|------------|---------------------|-------|
| production  | sqlite     | false               | YES   |
| production  | sqlite     | true                | no    |
| production  | postgres   | false               | no    |
| development | sqlite     | false               | no    |
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from awaithumans.server.app import _warn_if_ephemeral_sqlite_in_production
from awaithumans.server.core.config import settings


@pytest.fixture
def _restore_settings() -> Iterator[None]:
    """Snapshot + restore the four Settings fields the helper reads."""
    original = (
        settings.ENVIRONMENT,
        settings.DATABASE_URL,
        settings.DB_PATH,
        settings.ALLOW_EPHEMERAL_DB,
    )
    yield
    (
        settings.ENVIRONMENT,
        settings.DATABASE_URL,
        settings.DB_PATH,
        settings.ALLOW_EPHEMERAL_DB,
    ) = original


def test_warns_in_production_with_sqlite_default(
    _restore_settings: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The headline case: production + SQLite default + no
    acknowledgement → one WARNING record containing the actionable
    fix-up instructions.

    Pins the message contents so an over-eager future cleanup can't
    accidentally truncate the banner down to a one-liner that
    operators skim past. The whole point of the verbose banner is
    that an unfamiliar operator can fix the issue without leaving
    the terminal.
    """
    settings.ENVIRONMENT = "production"
    settings.DATABASE_URL = None
    settings.DB_PATH = "/var/lib/awaithumans/awaithumans.db"
    settings.ALLOW_EPHEMERAL_DB = False

    with caplog.at_level(logging.WARNING, logger="awaithumans.server"):
        _warn_if_ephemeral_sqlite_in_production()

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1, (
        f"Expected exactly one WARNING record, got {len(warning_records)}"
    )

    msg = warning_records[0].getMessage()
    # Headline marker — substring searches in log pipelines need it.
    assert "production environment is running on SQLite" in msg
    # Actionable remediations.
    assert "AWAITHUMANS_DATABASE_URL" in msg
    assert "AWAITHUMANS_DB_PATH" in msg
    assert "AWAITHUMANS_ALLOW_EPHEMERAL_DB=true" in msg
    # The resolved DB_PATH so the operator knows which file is at risk.
    assert "/var/lib/awaithumans/awaithumans.db" in msg


def test_warns_when_database_url_is_sqlite_scheme(
    _restore_settings: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicit ``sqlite://`` or ``sqlite+aiosqlite://`` DATABASE_URL
    still triggers the warning — the scheme, not just the
    DATABASE_URL-is-None default, determines whether we're on SQLite.

    Without this check, an operator who set
    ``AWAITHUMANS_DATABASE_URL=sqlite:///./prod.db`` to be \"explicit\"
    would silently get the same data-loss risk with no warning.
    """
    settings.ENVIRONMENT = "production"
    settings.DATABASE_URL = "sqlite+aiosqlite:///./prod.db"
    settings.DB_PATH = ".awaithumans/dev.db"
    settings.ALLOW_EPHEMERAL_DB = False

    with caplog.at_level(logging.WARNING, logger="awaithumans.server"):
        _warn_if_ephemeral_sqlite_in_production()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "production environment is running on SQLite" in warnings[0].getMessage()


def test_silent_in_production_with_postgres(
    _restore_settings: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Postgres deployments don't have the failure mode the banner
    warns about. Stay quiet — log spam is its own anti-pattern."""
    settings.ENVIRONMENT = "production"
    settings.DATABASE_URL = "postgresql://u:p@host:5432/db"
    settings.DB_PATH = ".awaithumans/dev.db"
    settings.ALLOW_EPHEMERAL_DB = False

    with caplog.at_level(logging.WARNING, logger="awaithumans.server"):
        _warn_if_ephemeral_sqlite_in_production()

    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "Postgres deployments must not emit the ephemeral-SQLite warning"
    )


def test_silent_in_development_even_with_sqlite(
    _restore_settings: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``awaithumans dev`` boots into SQLite by design — that's not a
    production durability concern, so don't warn. The check is
    gated on ``settings.is_production`` precisely so dev startup
    stays quiet."""
    settings.ENVIRONMENT = "development"
    settings.DATABASE_URL = None
    settings.DB_PATH = ".awaithumans/dev.db"
    settings.ALLOW_EPHEMERAL_DB = False

    with caplog.at_level(logging.WARNING, logger="awaithumans.server"):
        _warn_if_ephemeral_sqlite_in_production()

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_silenced_by_allow_ephemeral_db_acknowledgement(
    _restore_settings: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``AWAITHUMANS_ALLOW_EPHEMERAL_DB=true`` is the operator's
    signed acknowledgement that durability is handled elsewhere
    (mounted volume, accepted-risk dev preview, etc.). The warning
    is replaced by a single INFO record so the decision is still
    auditable.

    This pin matters because the env-var name is the contract: a
    misspelling like ALLOWED_EPHEMERAL_DB or ALLOW_EPHEMERAL_DATABASE
    would silently fail to suppress the warning and confuse
    operators who think they configured the knob.
    """
    settings.ENVIRONMENT = "production"
    settings.DATABASE_URL = None
    settings.DB_PATH = "/var/lib/awaithumans/awaithumans.db"
    settings.ALLOW_EPHEMERAL_DB = True

    with caplog.at_level(logging.INFO, logger="awaithumans.server"):
        _warn_if_ephemeral_sqlite_in_production()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, "Acknowledged ephemeral DB must not emit a WARNING"

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("operator-acknowledged" in r.getMessage() for r in infos), (
        f"Expected an INFO acknowledgement record; got: {[r.getMessage() for r in infos]!r}"
    )


def test_warning_fires_from_lifespan_in_production(
    _restore_settings: None,
) -> None:
    """End-to-end check: the lifespan helper actually calls the
    ephemeral-SQLite check on the production path.

    Without this, refactoring the call site (e.g. moving it into a
    different helper) could silently drop the check while
    ``_warn_if_ephemeral_sqlite_in_production`` itself still works.
    The test mocks heavy startup bits so it focuses on the call
    relationship, not the DB.

    Spies on the module-level logger via ``patch.object`` instead of
    using ``caplog`` because ``setup_logging()`` clears root handlers
    on every ``create_app()``, which evicts caplog's handler and
    would make this assertion a false negative.
    """
    import asyncio
    import secrets
    from unittest.mock import AsyncMock

    from awaithumans.server import app as app_module
    from awaithumans.server.app import create_app, lifespan
    from awaithumans.server.core import encryption

    settings.ENVIRONMENT = "production"
    settings.DATABASE_URL = None
    settings.DB_PATH = "/var/lib/awaithumans/awaithumans.db"
    settings.ALLOW_EPHEMERAL_DB = False
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()

    fake_init = AsyncMock(return_value=None)
    fake_first_run = AsyncMock(return_value=None)

    app = create_app()

    async def _drive() -> None:
        async with lifespan(app):
            pass

    with (
        patch("awaithumans.server.app.init_db", fake_init),
        patch("awaithumans.server.app._first_run_setup_url", fake_first_run),
        patch.object(app_module.logger, "warning") as mock_warning,
    ):
        asyncio.run(_drive())

    # The helper makes exactly one logger.warning call.
    warning_payloads = [" ".join(str(a) for a in c.args) for c in mock_warning.call_args_list]
    combined = "\n".join(warning_payloads)
    assert "production environment is running on SQLite" in combined, (
        f"Expected ephemeral-SQLite warning during lifespan; got: {combined!r}"
    )
