"""Per-test isolated SQLite + PAYLOAD_KEY + seeded operator user.

After PR A3 the dashboard auth is DB-backed — every auth test needs a
real User row to log in against. The `operator_user` fixture inserts
one via the user service so tests look like the production path.
"""

from __future__ import annotations

import asyncio
import secrets
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings
from awaithumans.server.db.models import User

# Default operator credentials used across the auth test suite.
OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _isolated_db() -> Iterator[None]:
    """Route every test in this module to a fresh SQLite tempfile."""
    import awaithumans.server.db.connection as conn
    from awaithumans.server.core import bootstrap

    original_db_path = settings.DB_PATH
    original_db_url = settings.DATABASE_URL
    original_engine = conn._async_engine
    original_factory = conn._async_session_factory

    tmpdir = tempfile.mkdtemp()
    settings.DB_PATH = str(Path(tmpdir) / "test.db")
    settings.DATABASE_URL = None
    conn._async_engine = None
    conn._async_session_factory = None

    # The bootstrap flag is module state — reset it so each test
    # starts from "no setup token generated yet."
    bootstrap._token = None
    bootstrap._completed = False

    yield

    settings.DB_PATH = original_db_path
    settings.DATABASE_URL = original_db_url
    conn._async_engine = original_engine
    conn._async_session_factory = original_factory


@pytest.fixture(autouse=True)
def _payload_key() -> Iterator[None]:
    """Every auth test needs PAYLOAD_KEY — sessions and encrypted columns
    both derive their keys from it.

    We swap BOTH the pydantic-settings copy AND the `os.environ` value
    because the new `awaithumans.utils.webhook_signing` module (PR #71)
    reads directly from `os.environ` to stay free of the [server]
    extra. Without the env-var swap, a test that signs a body via the
    server gets a different HMAC key than a test that verifies via the
    adapter, and signature checks flap."""
    import os

    from awaithumans.utils import webhook_signing

    fresh = secrets.token_urlsafe(32)
    original_settings = settings.PAYLOAD_KEY
    original_env = os.environ.get("AWAITHUMANS_PAYLOAD_KEY")

    settings.PAYLOAD_KEY = fresh
    os.environ["AWAITHUMANS_PAYLOAD_KEY"] = fresh
    encryption.reset_key_cache()
    webhook_signing.reset_cache()
    yield
    settings.PAYLOAD_KEY = original_settings
    if original_env is None:
        os.environ.pop("AWAITHUMANS_PAYLOAD_KEY", None)
    else:
        os.environ["AWAITHUMANS_PAYLOAD_KEY"] = original_env
    encryption.reset_key_cache()
    webhook_signing.reset_cache()


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    """Module-level rate-limiter singletons leak state across tests
    that share a process. Without this reset, the 11th test that
    posts to /login from the same client IP hits the global
    counter and starts seeing 429s instead of the expected 401."""
    from awaithumans.server.core import rate_limit

    rate_limit.LOGIN_PER_IP._hits.clear()
    rate_limit.LOGIN_PER_EMAIL._hits.clear()
    rate_limit.SETUP_PER_IP._hits.clear()
    yield
    rate_limit.LOGIN_PER_IP._hits.clear()
    rate_limit.LOGIN_PER_EMAIL._hits.clear()
    rate_limit.SETUP_PER_IP._hits.clear()


@pytest.fixture
def operator_user() -> Iterator[User]:
    """Insert a fresh operator into the test DB and return the row.

    Runs migrations + creates the user in one short-lived loop so tests
    can log in via `POST /api/auth/login` immediately after."""
    from awaithumans.server.db.connection import (
        close_db,
        get_async_session_factory,
        init_db,
    )
    from awaithumans.server.services.user_service import create_user

    async def _seed() -> User:
        await init_db()
        factory = get_async_session_factory()
        async with factory() as session:
            return await create_user(
                session,
                email=OPERATOR_EMAIL,
                display_name="Test Operator",
                is_operator=True,
                password=OPERATOR_PASSWORD,
            )

    user = asyncio.new_event_loop().run_until_complete(_seed())

    yield user

    async def _teardown() -> None:
        await close_db()

    asyncio.new_event_loop().run_until_complete(_teardown())
