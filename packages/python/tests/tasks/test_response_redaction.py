"""Schema-level round-trip for the response-redaction flag.

The deeper webhook-dispatch tests in ``tests/core/test_webhook_dispatch.py``
cover the actual redaction-on-callback behavior end to end. This file
pins the simpler HTTP-surface contract: POST /api/tasks accepts the
new ``redact_response_after_submit`` field, the Task row stores it,
and GET /api/tasks/{id} echoes both ``redact_response_after_submit``
AND the (initially-null) ``response_redacted_at`` so the dashboard
can read them.

Mirrors the existing ``test_task_metadata.py`` / ``test_initial_response.py``
round-trip pattern.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.tasks.test_route_authorization import (
    _admin_headers,
    client,  # noqa: F401  — fixture re-export
)

_BODY: dict = {
    "task": "Verify invoice",
    "payload": {"document_url": "https://example.com/inv.pdf"},
    "payload_schema": {"type": "object"},
    "response_schema": {"type": "object"},
    "timeout_seconds": 900,
}


def test_redact_response_flag_persists_when_true(client: TestClient) -> None:  # noqa: F811
    """``redact_response_after_submit=true`` round-trips through
    POST → DB → GET. Pre-fix, the field didn't exist; this test
    pins the contract managed relies on (the wire-shape).
    """
    body = {
        **_BODY,
        "idempotency_key": "redact-flag-true",
        "redact_response_after_submit": True,
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.text
    assert resp.json()["redact_response_after_submit"] is True

    task_id = resp.json()["id"]
    fetched = client.get(f"/api/tasks/{task_id}", headers=_admin_headers())
    assert fetched.status_code == 200
    assert fetched.json()["redact_response_after_submit"] is True
    # No callback fired yet → response_redacted_at is null.
    assert fetched.json()["response_redacted_at"] is None


def test_redact_response_flag_defaults_to_false(client: TestClient) -> None:  # noqa: F811
    """The flag is optional on the wire and defaults to false. Non-
    AwaitVerify callers that never set it must see the default
    behavior preserved — response stays in DB after submit.
    """
    body = {**_BODY, "idempotency_key": "redact-flag-default"}
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.text
    assert resp.json()["redact_response_after_submit"] is False
    assert resp.json()["response_redacted_at"] is None


def test_redact_response_flag_explicit_false_still_false(
    client: TestClient,  # noqa: F811
) -> None:
    """Explicit false in the wire body matches the default — no
    surprise truthy coercion."""
    body = {
        **_BODY,
        "idempotency_key": "redact-flag-false",
        "redact_response_after_submit": False,
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201
    assert resp.json()["redact_response_after_submit"] is False


def test_response_redacted_at_in_get_response(client: TestClient) -> None:  # noqa: F811
    """The field is part of TaskResponse on every GET regardless of
    redaction state. The dashboard reads it to choose between the
    structured read-back and the "delivered" placeholder.
    """
    body = {
        **_BODY,
        "idempotency_key": "redact-readback-shape",
        "redact_response_after_submit": True,
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    task_id = resp.json()["id"]

    fetched = client.get(f"/api/tasks/{task_id}", headers=_admin_headers())
    data = fetched.json()
    assert "response_redacted_at" in data
    assert "redact_response_after_submit" in data
