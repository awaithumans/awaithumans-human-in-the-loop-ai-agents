"""Shared fixtures for HTTP route tests.

Re-exports the auth-package fixtures so route tests pick up the same
isolated SQLite + PAYLOAD_KEY scaffolding the rest of the suite uses.
Tests that need a TestClient can build one inline against
``create_app(serve_dashboard=False)``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from awaithumans.server.app import create_app
from awaithumans.server.core.config import settings
from tests.auth.conftest import (  # noqa: F401 (fixture re-export)
    _isolated_db,
    _payload_key,
    _reset_rate_limit,
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Boot the FastAPI app and yield a TestClient against it.

    Runs the lifespan (alembic migrations on the per-test SQLite from
    ``_isolated_db``) so the ``demo_records`` table exists before the
    first request. Dashboard static mount is disabled because route
    tests only care about the API surface.

    The Turnstile secret is set to a non-empty placeholder so the
    Turnstile-verify code path doesn't bail with the "secret not
    configured" early return; the test then monkeypatches the network
    call itself to a no-op.
    """
    original_secret = settings.TURNSTILE_SECRET
    settings.TURNSTILE_SECRET = "test-turnstile-secret"
    app = create_app(serve_dashboard=False)
    with TestClient(app) as c:
        yield c
    settings.TURNSTILE_SECRET = original_secret
