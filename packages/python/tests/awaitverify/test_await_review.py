"""Tests for await_review — the no-document text/data review entry point.

await_review skips the upload/fragment/encrypt path entirely: it creates
a managed task with no upload_session_id and polls for the reviewer's
typed response. These tests stub the two managed-client calls it uses
(create_task + poll) so the loop runs without a backend.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from awaithumans.awaitverify import client as client_mod
from awaithumans.instance import AwaitHumans


class _Decision(BaseModel):
    approved: bool
    reason: str | None = None


def _stub_review_calls(
    monkeypatch: pytest.MonkeyPatch,
    *,
    completed_response: dict[str, Any] | None = None,
    completed_status: str = "completed",
) -> dict[str, Any]:
    """Stub create_task + poll. Also stub the upload helpers to FAIL so
    a regression that reintroduces the document path is caught."""
    captured: dict[str, Any] = {"task_request": None, "polls": 0}

    async def fail_upload_session(**_: Any) -> Any:
        raise AssertionError("await_review must not create an upload session")

    async def fail_upload_fragment(**_: Any) -> Any:
        raise AssertionError("await_review must not upload fragments")

    async def fake_create_task(
        *,
        managed_url: str,
        api_key: str | None,
        upload_session_id: str | None,
        task_description: str,
        response_schema_json: str,
        priority: str,
        task_metadata: dict[str, str] | None = None,
        initial_response: dict[str, Any] | None = None,
    ) -> Any:
        from awaithumans.awaitverify._managed_client import CreatedTask

        captured["task_request"] = {
            "upload_session_id": upload_session_id,
            "task_description": task_description,
            "response_schema_json": response_schema_json,
            "priority": priority,
            "task_metadata": task_metadata,
            "initial_response": initial_response,
        }
        # The managed backend returns the placeholder session id.
        return CreatedTask(
            task_id="task-id-fake",
            upload_session_id="placeholder-sess",
            status="awaiting_review",
        )

    async def fake_poll_task(
        *, managed_url: str, api_key: str | None, task_id: str, timeout_seconds: int
    ) -> Any:
        from awaithumans.awaitverify._managed_client import PolledTask

        captured["polls"] += 1
        response_json = (
            json.dumps(completed_response)
            if completed_response is not None and completed_status == "completed"
            else None
        )
        return PolledTask(task_id=task_id, status=completed_status, response_json=response_json)

    monkeypatch.setattr(client_mod, "_managed_create_upload_session", fail_upload_session)
    monkeypatch.setattr(client_mod, "_managed_upload_fragment", fail_upload_fragment)
    monkeypatch.setattr(client_mod, "_managed_create_task", fake_create_task)
    monkeypatch.setattr(client_mod, "_managed_poll_task", fake_poll_task)
    return captured


@pytest.mark.asyncio
async def test_await_review_creates_task_with_no_upload_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_review_calls(
        monkeypatch, completed_response={"approved": True, "reason": "looks fine"}
    )
    client = AwaitHumans(api_key="ah_sk_test", managed_url="http://localhost:8000")

    result = await client.await_review(
        task_description="Approve this $4,000 refund?",
        response_schema=_Decision,
        task_metadata={"customer": "Acme", "amount": "$4,000"},
    )

    # No document: the task was created with no upload session.
    assert captured["task_request"]["upload_session_id"] is None
    # The instruction + context flowed through.
    assert captured["task_request"]["task_description"] == "Approve this $4,000 refund?"
    assert captured["task_request"]["task_metadata"] == {
        "customer": "Acme",
        "amount": "$4,000",
    }
    # No prefilled response on a pure review.
    assert captured["task_request"]["initial_response"] is None
    # The reviewer's typed response is validated against the schema.
    assert isinstance(result, _Decision)
    assert result.approved is True
    assert result.reason == "looks fine"


@pytest.mark.asyncio
async def test_await_review_module_shim_uses_default_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import awaithumans

    awaithumans.set_default_client(
        AwaitHumans(api_key="ah_sk_default", managed_url="http://localhost:8000")
    )
    captured = _stub_review_calls(monkeypatch, completed_response={"approved": False})

    result = await awaithumans.await_review(
        task_description="Is this spam?", response_schema=_Decision
    )

    assert captured["task_request"]["upload_session_id"] is None
    assert isinstance(result, _Decision)
    assert result.approved is False


@pytest.mark.asyncio
async def test_await_review_propagates_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from awaithumans.awaitverify.client import VerifyTaskNonTerminalError

    _stub_review_calls(monkeypatch, completed_status="timed_out")
    client = AwaitHumans(api_key="ah_sk_test")

    with pytest.raises(VerifyTaskNonTerminalError):
        await client.await_review(task_description="Approve?", response_schema=_Decision)
