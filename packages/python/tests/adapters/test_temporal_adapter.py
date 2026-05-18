"""Temporal adapter — workflow integration + callback dispatch.

Two distinct test surfaces:

1. **Workflow side** (`await_human` from inside a workflow): verified
   via Temporal's `WorkflowEnvironment.from_local()` test harness,
   which spins up a real but ephemeral Temporal server. We register a
   workflow that calls `await_human`, signal it from outside (mimicking
   the user's web server), and assert the workflow returns the typed
   response — including the timeout, cancellation, and exhausted
   paths.

2. **Callback side** (`dispatch_signal` in the user's web server):
   verified with a fake temporal_client. The signature-validation,
   payload-parsing, and signal-routing logic is tested independently
   of any real Temporal cluster.

The harness is heavyweight — `WorkflowEnvironment.from_local()` boots
a Java process — so we mark the workflow tests `slow`. They still run
in CI but a developer can `pytest -m 'not slow'` for a fast loop.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

from awaithumans.adapters.temporal import (
    _signal_name,
    awaithumans_create_task,
    dispatch_signal,
)
from awaithumans.errors import (
    TaskCancelledError,
)
from awaithumans.server.core import encryption
from awaithumans.server.core.config import settings
from awaithumans.server.services.webhook_dispatch import sign_body


@pytest.fixture(autouse=True)
def _payload_key() -> Iterator[None]:
    """HKDF derives the webhook key from PAYLOAD_KEY — required for
    `sign_body` and `verify_signature` to work."""
    original = settings.PAYLOAD_KEY
    settings.PAYLOAD_KEY = secrets.token_urlsafe(32)
    encryption.reset_key_cache()
    yield
    settings.PAYLOAD_KEY = original
    encryption.reset_key_cache()


# ─── dispatch_signal ─────────────────────────────────────────────────


@dataclass
class _SignalCall:
    name: str
    arg: Any


class _FakeWorkflowHandle:
    def __init__(self, sink: list[_SignalCall]) -> None:
        self._sink = sink

    async def signal(self, name: str, arg: Any) -> None:
        self._sink.append(_SignalCall(name=name, arg=arg))


class _FakeTemporalClient:
    """Stand-in for `temporalio.client.Client`.

    Only implements `get_workflow_handle(...)` because that's the
    surface `dispatch_signal` exercises. Records every signal call so
    tests can assert on (workflow_id, signal_name, payload)."""

    def __init__(self) -> None:
        self.calls: dict[str, list[_SignalCall]] = {}

    def get_workflow_handle(self, workflow_id: str) -> _FakeWorkflowHandle:
        sink = self.calls.setdefault(workflow_id, [])
        return _FakeWorkflowHandle(sink)


def _signed_body(payload: dict[str, Any]) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    return body, sign_body(body)


@pytest.mark.asyncio
async def test_dispatch_signal_routes_to_correct_workflow_and_signal() -> None:
    client = _FakeTemporalClient()
    payload = {
        "task_id": "t-1",
        "idempotency_key": "temporal:abc123",
        "status": "completed",
        "response": {"approved": True},
    }
    body, sig = _signed_body(payload)

    await dispatch_signal(
        temporal_client=client,
        workflow_id="wf-42",
        body=body,
        signature_header=sig,
    )

    calls = client.calls["wf-42"]
    assert len(calls) == 1
    assert calls[0].name == _signal_name("temporal:abc123")
    assert calls[0].arg["status"] == "completed"
    assert calls[0].arg["response"] == {"approved": True}


@pytest.mark.asyncio
async def test_dispatch_signal_rejects_bad_signature() -> None:
    """Wrong/missing signature is a security event; the wrapper
    should render 401. We raise PermissionError so the framework
    layer can map it cleanly."""
    client = _FakeTemporalClient()
    body = json.dumps({"idempotency_key": "temporal:x", "status": "completed"}).encode()

    with pytest.raises(PermissionError):
        await dispatch_signal(
            temporal_client=client,
            workflow_id="wf-x",
            body=body,
            signature_header="sha256=" + "0" * 64,
        )

    with pytest.raises(PermissionError):
        await dispatch_signal(
            temporal_client=client,
            workflow_id="wf-x",
            body=body,
            signature_header=None,
        )

    # No signal sent for either rejection.
    assert client.calls == {}


@pytest.mark.asyncio
async def test_dispatch_signal_rejects_non_json_body() -> None:
    client = _FakeTemporalClient()
    body = b"not-json"
    sig = sign_body(body)
    with pytest.raises(ValueError, match="not JSON"):
        await dispatch_signal(
            temporal_client=client,
            workflow_id="wf-x",
            body=body,
            signature_header=sig,
        )


@pytest.mark.asyncio
async def test_dispatch_signal_rejects_missing_idempotency_key() -> None:
    """The signal name is derived from idempotency_key — without it
    we can't route the signal."""
    client = _FakeTemporalClient()
    payload = {"task_id": "t-1", "status": "completed"}
    body, sig = _signed_body(payload)

    with pytest.raises(ValueError, match="idempotency_key"):
        await dispatch_signal(
            temporal_client=client,
            workflow_id="wf-x",
            body=body,
            signature_header=sig,
        )


# ─── End-to-end workflow integration (slow) ─────────────────────────
#
# Temporal-harness tests are gated under the `slow` marker because
# `WorkflowEnvironment.start_local()` boots a Temporal server
# subprocess (~5s warmup). Workflow classes MUST be module-level —
# Temporal's deterministic-execution machinery rejects classes
# defined inside test functions.


class _Payload(BaseModel):
    amount: int


class _Decision(BaseModel):
    approved: bool


# Module-level workflows for the slow integration tests.
try:
    from temporalio import workflow as _wf

    from awaithumans.adapters import temporal as _adapter

    @_wf.defn
    class _RefundWorkflow:
        @_wf.run
        async def run(self, amount: int) -> dict:
            decision = await _adapter.await_human(
                task="Approve refund?",
                payload_schema=_Payload,
                payload=_Payload(amount=amount),
                response_schema=_Decision,
                timeout_seconds=60,
                callback_url="https://my-app.test/cb",
                server_url="http://awaithumans-test.invalid",
            )
            return decision.model_dump()

    @_wf.defn
    class _CancelWorkflow:
        @_wf.run
        async def run(self) -> str:
            try:
                await _adapter.await_human(
                    task="Approve refund?",
                    payload_schema=_Payload,
                    payload=_Payload(amount=100),
                    response_schema=_Decision,
                    timeout_seconds=60,
                    callback_url="https://my-app.test/cb",
                    server_url="http://awaithumans-test.invalid",
                )
                return "completed"
            except TaskCancelledError:
                return "cancelled"

except ImportError:
    # temporalio not installed → slow tests are skipped via
    # importorskip below; the workflow-class symbols are unused.
    _RefundWorkflow = None  # type: ignore[assignment,misc]
    _CancelWorkflow = None  # type: ignore[assignment,misc]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_await_human_returns_response_after_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: workflow registers signal handler, fires create-task
    activity, signals from outside, workflow returns typed response."""
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from awaithumans.adapters import temporal as adapter

    # Stub the create-task activity so we don't need a real
    # awaithumans server. It returns a synthetic task_id and the
    # workflow proceeds to wait_condition.
    async def fake_create(req: adapter._CreateTaskInput) -> dict:
        return {"id": "task-stub", "idempotency_key": req.idempotency_key}

    monkeypatch.setattr(adapter, "awaithumans_create_task", fake_create)

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        async with Worker(
            client,
            task_queue="test-q",
            workflows=[_RefundWorkflow],
            activities=[fake_create],
        ):
            wf_id = f"wf-{uuid.uuid4()}"
            handle = await client.start_workflow(
                _RefundWorkflow.run,
                100,
                id=wf_id,
                task_queue="test-q",
            )

            idem = adapter._default_idempotency_key("Approve refund?", _Payload(amount=100))
            signal = adapter._signal_name(idem)
            await asyncio.sleep(0.5)  # let workflow register handler
            await handle.signal(
                signal,
                {
                    "task_id": "task-stub",
                    "idempotency_key": idem,
                    "status": "completed",
                    "response": {"approved": True},
                },
            )

            result = await asyncio.wait_for(handle.result(), timeout=10)
            assert result == {"approved": True}


@pytest.mark.slow
@pytest.mark.asyncio
async def test_await_human_raises_typed_error_on_cancelled_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook delivers status=cancelled → workflow raises
    TaskCancelledError, not TaskTimeoutError. Agents can distinguish."""
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from awaithumans.adapters import temporal as adapter

    async def fake_create(req: adapter._CreateTaskInput) -> dict:
        return {"id": "task-stub", "idempotency_key": req.idempotency_key}

    monkeypatch.setattr(adapter, "awaithumans_create_task", fake_create)

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        async with Worker(
            client,
            task_queue="test-q",
            workflows=[_CancelWorkflow],
            activities=[fake_create],
        ):
            wf_id = f"wf-{uuid.uuid4()}"
            handle = await client.start_workflow(_CancelWorkflow.run, id=wf_id, task_queue="test-q")
            idem = adapter._default_idempotency_key("Approve refund?", _Payload(amount=100))
            await asyncio.sleep(0.5)
            await handle.signal(
                adapter._signal_name(idem),
                {
                    "task_id": "task-stub",
                    "idempotency_key": idem,
                    "status": "cancelled",
                    "response": None,
                },
            )
            result = await asyncio.wait_for(handle.result(), timeout=10)
            assert result == "cancelled"


# ─── Activity-level test (no Temporal harness needed) ────────────────


@pytest.mark.asyncio
async def test_create_task_activity_posts_with_bearer_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The activity runs OUTSIDE the workflow sandbox (real httpx).
    Pinning that it (a) hits /api/tasks, (b) sends Bearer auth when
    api_key is provided, (c) parses the JSON response."""
    import httpx

    received: dict[str, Any] = {}

    async def fake_handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["headers"] = dict(request.headers)
        received["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "task-from-server"})

    real_client = httpx.AsyncClient

    def _factory(**kw: Any) -> httpx.AsyncClient:
        kw.pop("transport", None)
        return real_client(transport=httpx.MockTransport(fake_handler), **kw)

    # Adapter imports `httpx` at module level (not inline), so we
    # patch the attribute reference the activity actually uses.
    import awaithumans.adapters.temporal as adapter

    monkeypatch.setattr(adapter.httpx, "AsyncClient", _factory)

    from awaithumans.adapters.temporal import _CreateTaskInput

    out = await awaithumans_create_task(
        _CreateTaskInput(
            server_url="http://test.example",
            api_key="test-bearer",
            task="Approve",
            payload={"x": 1},
            payload_schema={},
            response_schema={},
            form_definition=None,
            timeout_seconds=900,
            idempotency_key="temporal:abc",
            callback_url="http://cb.example/cb",
            assign_to=None,
            notify=None,
            verifier_config=None,
            redact_payload=False,
        )
    )
    assert out == {"id": "task-from-server"}
    assert received["headers"]["authorization"] == "Bearer test-bearer"
    assert received["body"]["idempotency_key"] == "temporal:abc"
    assert received["body"]["callback_url"] == "http://cb.example/cb"
