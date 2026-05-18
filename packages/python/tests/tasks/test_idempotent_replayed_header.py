"""POST /api/tasks sets `Idempotent-Replayed: true` on idempotency hits.

Stripe-style replay convention: status stays 201 (clients that check
`status == 201` for success don't break), but a header lets aware
clients detect whether they got a fresh task or the same one they
already created earlier.

Test user flagged the broader question (should we flip 201 → 200 on
replay?) — answer is no, keep the status, add the header. This file
pins that decision so anyone tempted to "fix" the status code later
breaks a visible test.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.tasks.test_route_authorization import (  # fixture re-exports
    _admin_headers,
    client,  # noqa: F401
)

_BODY: dict = {
    "task": "Approve refund",
    "payload": {"amount": 100},
    "payload_schema": {"type": "object"},
    "response_schema": {"type": "object"},
    "timeout_seconds": 900,
}


def test_first_call_has_no_replayed_header(client: TestClient) -> None:  # noqa: F811
    body = {**_BODY, "idempotency_key": "fresh-key-1"}
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201
    assert "idempotent-replayed" not in {k.lower() for k in resp.headers}, (
        "Fresh creation must NOT carry the replay header — clients use its "
        "absence to confirm they got a new resource."
    )


def test_replay_carries_idempotent_replayed_header(client: TestClient) -> None:  # noqa: F811
    body = {**_BODY, "idempotency_key": "replay-key-2"}

    first = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert first.status_code == 201
    assert "idempotent-replayed" not in {k.lower() for k in first.headers}

    second = client.post("/api/tasks", json=body, headers=_admin_headers())
    # Status stays 201 (Stripe convention) — flipping to 200 would
    # break clients that check the specific code.
    assert second.status_code == 201
    assert second.headers["idempotent-replayed"] == "true", (
        f"Expected header on replay, headers were: {dict(second.headers)}"
    )

    # Same row both times — replay must return the task that was
    # created on the first call.
    assert second.json()["id"] == first.json()["id"]


def test_replay_value_is_lowercase_true(client: TestClient) -> None:  # noqa: F811
    """Wire format pinned: literal `true` (lowercase, JSON-style),
    not `True` / `1` / `yes`. Stripe uses `true`, and clients should
    only ever need a single equality check."""
    body = {**_BODY, "idempotency_key": "value-shape-3"}
    client.post("/api/tasks", json=body, headers=_admin_headers())
    replay = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert replay.headers["idempotent-replayed"] == "true"
