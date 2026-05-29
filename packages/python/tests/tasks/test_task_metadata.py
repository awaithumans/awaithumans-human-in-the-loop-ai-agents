"""task_metadata round-trip — POST → DB → GET → Slack block.

Covers:
- CreateTaskRequest accepts task_metadata
- Task row stores it
- TaskResponse echoes it
- Idempotency replay returns the same metadata
- Slack block renderers include a Context block when metadata is set
- Slack block renderers skip the Context block when metadata is empty
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from awaithumans.forms import FormDefinition, Switch
from awaithumans.server.channels.slack.blocks.surfaces import (
    form_to_modal,
    open_review_message_blocks,
    task_metadata_blocks,
)
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


# ─── HTTP round-trip ────────────────────────────────────────────────────


def test_metadata_persists_on_create(client: TestClient) -> None:  # noqa: F811
    body = {
        **_BODY,
        "idempotency_key": "meta-key-1",
        "task_metadata": {
            "customer": "Acme Corp",
            "vertical": "construction",
            "tier": "enterprise",
        },
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201

    task_id = resp.json()["id"]
    fetched = client.get(f"/api/tasks/{task_id}", headers=_admin_headers())
    assert fetched.status_code == 200
    assert fetched.json()["task_metadata"] == {
        "customer": "Acme Corp",
        "vertical": "construction",
        "tier": "enterprise",
    }


def test_metadata_absent_is_null(client: TestClient) -> None:  # noqa: F811
    """A task created without metadata should serialize the field as
    null — clients can rely on it being a stable optional rather
    than a missing key."""
    body = {**_BODY, "idempotency_key": "meta-key-2"}
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 201
    assert resp.json()["task_metadata"] is None


def test_idempotency_replay_returns_original_metadata(
    client: TestClient,  # noqa: F811
) -> None:
    """When a replay hits an existing task, the response should
    surface the metadata recorded at create time — not whatever the
    second caller sent. Otherwise a retry with a different metadata
    payload would silently lie about what the reviewer sees."""
    first = client.post(
        "/api/tasks",
        json={
            **_BODY,
            "idempotency_key": "meta-replay-key",
            "task_metadata": {"customer": "Original"},
        },
        headers=_admin_headers(),
    )
    assert first.status_code == 201

    replay = client.post(
        "/api/tasks",
        json={
            **_BODY,
            "idempotency_key": "meta-replay-key",
            "task_metadata": {"customer": "DifferentValue"},
        },
        headers=_admin_headers(),
    )
    assert replay.status_code == 201
    assert replay.json()["task_metadata"] == {"customer": "Original"}


def test_metadata_rejects_nested_values(client: TestClient) -> None:  # noqa: F811
    """The schema is dict[str, str] — non-string values are rejected
    at the validation layer so the rendering surfaces never receive
    a shape they can't format."""
    body = {
        **_BODY,
        "idempotency_key": "meta-nested-rejected",
        "task_metadata": {"customer": {"name": "Acme"}},  # nested dict — invalid
    }
    resp = client.post("/api/tasks", json=body, headers=_admin_headers())
    assert resp.status_code == 422


# ─── Slack block rendering ─────────────────────────────────────────────


def test_task_metadata_blocks_empty_returns_empty_list() -> None:
    assert task_metadata_blocks(None) == []
    assert task_metadata_blocks({}) == []


def test_task_metadata_blocks_renders_context_block() -> None:
    blocks = task_metadata_blocks(
        {"customer": "Acme", "vertical": "construction"}
    )
    assert len(blocks) == 1
    assert blocks[0]["type"] == "context"
    text = blocks[0]["elements"][0]["text"]
    assert "*customer*: Acme" in text
    assert "*vertical*: construction" in text


def test_task_metadata_blocks_truncates_overflow() -> None:
    """More than 5 keys triggers an overflow indicator so the
    notification doesn't blow past Slack's block size cap."""
    metadata = {f"k{i}": f"v{i}" for i in range(8)}
    blocks = task_metadata_blocks(metadata)
    text = blocks[0]["elements"][0]["text"]
    assert "_+3 more_" in text


def test_open_review_message_includes_metadata_block() -> None:
    blocks = open_review_message_blocks(
        task_id="t1",
        task_title="Approve refund",
        review_url="https://example.com/task/t1",
        open_button_action_id="awaithumans.open_review",
        task_metadata={"customer": "Acme"},
    )
    block_types = [b["type"] for b in blocks]
    # The metadata context block sits between the header section
    # and the actions row.
    assert "context" in block_types
    assert block_types.index("context") < block_types.index("actions")


def test_open_review_message_no_context_when_no_metadata() -> None:
    blocks = open_review_message_blocks(
        task_id="t1",
        task_title="x",
        review_url="https://example.com/task/t1",
        open_button_action_id="awaithumans.open_review",
    )
    assert "context" not in [b["type"] for b in blocks]


def test_form_to_modal_includes_metadata_block() -> None:
    form = FormDefinition(fields=[Switch(name="approved", label="Approved")])
    view = form_to_modal(
        form=form,
        task_id="t1",
        task_title="Refund",
        task_payload=None,
        task_metadata={"customer": "Acme", "tier": "enterprise"},
    )
    block_types = [b["type"] for b in view["blocks"]]
    # Header → context (metadata) → form input(s)
    assert block_types[0] == "header"
    assert "context" in block_types
    assert block_types.index("context") < len(block_types) - 1
