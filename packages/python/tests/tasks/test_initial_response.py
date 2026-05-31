"""initial_response round-trip — POST → DB → GET.

Covers the AwaitVerify Flow A / Flow B pre-fill payload. Managed
validates the value against ``response_schema`` before forwarding
here, so the OSS server only needs to:

1. Accept ``initial_response`` on the create request,
2. Persist it on the Task row,
3. Surface it in the GET-task response so the dashboard can read it.

The dashboard then mounts the form with these values pre-populated
(O4 lands in a separate PR). For this PR we only test the round-trip.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.tasks.test_route_authorization import (  # fixture re-exports
    _admin_headers,
    client,  # noqa: F401
)

_BODY: dict = {
    "task": "Verify invoice",
    "payload": {"document_url": "https://example.com/inv.pdf"},
    "payload_schema": {"type": "object"},
    "response_schema": {"type": "object"},
    "timeout_seconds": 900,
}


def test_initial_response_persists_on_create(client: TestClient) -> None:  # noqa: F811
    """A flat dict initial_response round-trips through POST → DB → GET
    unchanged. This is the happy path for Flow A on a simple schema.
    """
    body = {
        **_BODY,
        "idempotency_key": "init-response-flat",
        "initial_response": {
            "vendor": "Acme Corp",
            "amount": 1250.0,
            "currency": "USD",
        },
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.text

    task_id = resp.json()["id"]
    fetched = client.get(f"/api/tasks/{task_id}", headers=_admin_headers())
    assert fetched.status_code == 200
    assert fetched.json()["initial_response"] == {
        "vendor": "Acme Corp",
        "amount": 1250.0,
        "currency": "USD",
    }


def test_initial_response_persists_nested_shape(client: TestClient) -> None:  # noqa: F811
    """The whole point of initial_response is to carry the customer's
    extraction — which is often a nested Pydantic model. Lists,
    nested objects, and mixed primitives must all survive the
    JSON round-trip intact.

    This is the shape the form-rendering PR (O3) reads to pre-fill
    repeatable_groups: each item in ``line_items`` becomes a row.
    """
    body = {
        **_BODY,
        "idempotency_key": "init-response-nested",
        "initial_response": {
            "vendor": "Acme Corp",
            "address": {"city": "Brooklyn", "zip": "11201"},
            "line_items": [
                {"sku": "A-1", "qty": 2, "unit_price": 9.99},
                {"sku": "B-9", "qty": 1, "unit_price": 49.0},
            ],
            "notes": None,
        },
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.text

    task_id = resp.json()["id"]
    fetched = client.get(f"/api/tasks/{task_id}", headers=_admin_headers())
    body_back = fetched.json()["initial_response"]
    assert body_back["address"] == {"city": "Brooklyn", "zip": "11201"}
    assert body_back["line_items"] == [
        {"sku": "A-1", "qty": 2, "unit_price": 9.99},
        {"sku": "B-9", "qty": 1, "unit_price": 49.0},
    ]
    assert body_back["notes"] is None


def test_initial_response_absent_is_null(client: TestClient) -> None:  # noqa: F811
    """Tasks created without a pre-computed extraction (pure-human
    review, non-AwaitVerify await_human calls) serialize the field
    as null. Stable optional rather than missing key — the dashboard
    branches on truthy vs falsy.
    """
    body = {**_BODY, "idempotency_key": "init-response-absent"}
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201
    assert resp.json()["initial_response"] is None


def test_initial_response_in_create_response(client: TestClient) -> None:  # noqa: F811
    """The 201 response from POST /api/tasks should echo the
    initial_response so the SDK / managed backend can confirm the
    value was accepted without an extra GET round-trip.
    """
    body = {
        **_BODY,
        "idempotency_key": "init-response-echo",
        "initial_response": {"hello": "world"},
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201
    assert resp.json()["initial_response"] == {"hello": "world"}


def test_idempotency_replay_returns_original_initial_response(
    client: TestClient,  # noqa: F811
) -> None:
    """A retry with the same idempotency_key returns the FIRST
    initial_response, not the second. Otherwise a flaky retry path
    could silently flip the pre-fill values the reviewer sees.

    Mirrors the existing task_metadata replay test — same contract.
    """
    first = client.post(
        "/api/tasks",
        json={
            **_BODY,
            "idempotency_key": "init-response-replay",
            "initial_response": {"vendor": "Original"},
        },
        headers=_admin_headers(),
    )
    assert first.status_code == 201

    replay = client.post(
        "/api/tasks",
        json={
            **_BODY,
            "idempotency_key": "init-response-replay",
            "initial_response": {"vendor": "DifferentValue"},
        },
        headers=_admin_headers(),
    )
    assert replay.status_code == 201
    assert replay.json()["initial_response"] == {"vendor": "Original"}
