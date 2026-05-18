"""Login + setup endpoints honour the rate limiter.

The primitive is tested in test_rate_limit.py; these tests exercise
the wire integration. Resetting the module-level singletons between
tests keeps each test independent."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from awaithumans.server.app import create_app
from awaithumans.server.core import rate_limit
from awaithumans.server.db.models import User

from .conftest import OPERATOR_EMAIL, OPERATOR_PASSWORD

# `_reset_rate_limit` autouse fixture lives in tests/auth/conftest.py
# so every test in this package gets a clean limiter state.


@pytest.fixture
def client(operator_user: User) -> Iterator[TestClient]:
    app = create_app(serve_dashboard=False)
    with TestClient(app) as c:
        yield c


# ─── Login ────────────────────────────────────────────────────────────


def test_login_429_after_per_email_limit(client: TestClient) -> None:
    """20 wrong-password attempts to the same email burns the
    per-email bucket (limit=20). The 21st returns 429 instead of 401."""
    rate_limit.LOGIN_PER_EMAIL.limit = 5  # tighten for fast test
    for _ in range(5):
        resp = client.post(
            "/api/auth/login",
            json={"email": OPERATOR_EMAIL, "password": "wrong"},
        )
        assert resp.status_code == 401
    resp = client.post(
        "/api/auth/login",
        json={"email": OPERATOR_EMAIL, "password": "wrong"},
    )
    assert resp.status_code == 429


def test_login_success_resets_per_email_counter(client: TestClient) -> None:
    """A correct login wipes the email's failure counter so a real
    user who fat-fingered three times then got it right doesn't get
    locked out 5 minutes later."""
    rate_limit.LOGIN_PER_EMAIL.limit = 3
    for _ in range(2):
        client.post(
            "/api/auth/login",
            json={"email": OPERATOR_EMAIL, "password": "wrong"},
        )

    # Right password — succeeds AND resets the counter
    ok = client.post(
        "/api/auth/login",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD},
    )
    assert ok.status_code == 204

    # Now they could fail another 3 times without being throttled
    rate_limit.LOGIN_PER_EMAIL._hits.clear()  # also IP — independent control
    for _ in range(3):
        resp = client.post(
            "/api/auth/login",
            json={"email": OPERATOR_EMAIL, "password": "wrong"},
        )
        assert resp.status_code == 401  # not 429


def test_login_429_after_per_ip_limit(client: TestClient) -> None:
    """Distinct emails from one IP hit the per-IP bucket — covers
    credential-stuffing where the attacker varies the email per
    request to avoid the per-email gate."""
    rate_limit.LOGIN_PER_IP.limit = 5
    for i in range(5):
        resp = client.post(
            "/api/auth/login",
            json={"email": f"user{i}@nope.com", "password": "x"},
        )
        # All unknown-email → 401, not 429 yet
        assert resp.status_code == 401
    resp = client.post(
        "/api/auth/login",
        json={"email": "user99@nope.com", "password": "x"},
    )
    assert resp.status_code == 429


# ─── Setup ────────────────────────────────────────────────────────────


def test_setup_429_after_per_ip_limit() -> None:
    """The unauth /setup/operator endpoint is the highest-risk
    rate-limit target because the bootstrap window is unbounded.
    Use a fresh app+DB for this test (no operator_user fixture so
    the route is reachable, even if its body fails token check)."""
    rate_limit.SETUP_PER_IP.limit = 3

    # Fresh app — needs PAYLOAD_KEY but no seeded user.
    import secrets

    from awaithumans.server.core import encryption
    from awaithumans.server.core.config import settings

    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()

    app = create_app(serve_dashboard=False)
    with TestClient(app) as client:
        body = {
            "token": "wrong",
            "email": "op@example.com",
            "display_name": "Op",
            "password": "correct-horse-battery",
        }
        for _ in range(3):
            resp = client.post("/api/setup/operator", json=body)
            # 403 (bad token) is what we want pre-limit, NOT 429
            assert resp.status_code in (403, 409, 422)
        resp = client.post("/api/setup/operator", json=body)
        assert resp.status_code == 429
