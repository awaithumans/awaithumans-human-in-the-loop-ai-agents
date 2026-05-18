"""LangGraph adapter — interrupt/resume durable HITL.

Two halves, deployed in two paths inside the same web process:

  1. **Inside a graph node** — the agent calls `await_human(...)` from
     this module. We POST the task to the awaithumans server and then
     call `interrupt(...)` from `langgraph.types`. That throws a
     `GraphInterrupt`; the caller (the user's app) catches it at the
     `graph.ainvoke()` boundary, persists nothing of its own (the
     checkpointer already saved state), and returns to its event loop.
     The process can crash and the state survives.

  2. **Inside the user's web server** — `dispatch_resume` is the
     framework-agnostic helper they wrap in a route. It verifies the
     HMAC, parses the body, and calls `graph.ainvoke(Command(resume=
     payload), config={"configurable": {"thread_id": ...}})`. The
     replayed `interrupt(...)` returns the response and the node
     continues.

Wire diagram:

    graph node               awaithumans server          user web server
    ──────────               ──────────────────          ───────────────
    await_human() ─────►     POST /api/tasks
                             store with callback_url
                             return task_id
    interrupt(taskId) ─x      (graph throws,
                              caller catches at .ainvoke)
                             ── human completes ──►
                             POST callback_url ──►       verify HMAC
                                                         graph.ainvoke(Command)
                                                         ─── replays node ───
                                                         interrupt() returns
                                                         await_human returns

Mirrors `awaithumans.adapters.langgraph` (TypeScript). Wire format
matches the Temporal adapter so a single awaithumans server can back
a mix of frameworks.

Idempotency note: the SDK's default key is `hash(task, payload)`,
which collides across graph threads. For per-thread tickets,
pass `idempotency_key=f"langgraph:{thread_id}:{node_name}"` so each
thread's interrupt creates its own server task and webhook URL.

Requires: `pip install "awaithumans[langgraph]"`. The whole module is
import-safe without langgraph installed; the call site fails-fast
with a clear ImportError when actually invoked.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from awaithumans.errors import (
    SchemaValidationError,
    TaskCancelledError,
    TaskTimeoutError,
    VerificationExhaustedError,
)
from awaithumans.types import VerifierConfig

logger = logging.getLogger("awaithumans.adapters.langgraph")

T = TypeVar("T", bound=BaseModel)

# Idempotency-key prefix — namespace so a content-hash collision
# across adapters can't accidentally land on the same task. Mirrors
# `temporal:` in the Temporal adapter.
_IDEMPOTENCY_PREFIX = "langgraph"


def _require_langgraph() -> None:
    """Lazy import gate — langgraph is an optional extra."""
    try:
        import langgraph  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The LangGraph adapter requires the [langgraph] extra.\n"
            'Install with: pip install "awaithumans[langgraph]"'
        ) from exc


def _default_idempotency_key(task: str, payload: BaseModel) -> str:
    """Deterministic key for a (task, payload) pair.

    Mirrors the direct-mode SDK's hashing so a node that does
    `await_human(task=..., payload=...)` produces the same key on
    every replay AND the same key as a non-LangGraph call to the
    same content.

    NOTE: This default collides across graph threads with identical
    content. For HITL across threads, pass an explicit
    `idempotency_key=f"{_IDEMPOTENCY_PREFIX}:{thread_id}:{node_name}"`.
    """
    canonical = json.dumps(
        {"task": task, "payload": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{_IDEMPOTENCY_PREFIX}:{hashlib.sha256(canonical.encode()).hexdigest()[:32]}"


def _serialize_assign_to(assign_to: object | None) -> dict[str, Any] | None:
    """Match the wire shape the server's Pydantic schema expects.
    Same logic as the Temporal adapter — kept inline rather than
    importing because the Temporal adapter's helper is in a different
    module path and cross-importing between adapters tangles the
    optional-extra story."""
    if assign_to is None:
        return None
    if isinstance(assign_to, str):
        return {"email": assign_to}
    if isinstance(assign_to, list):
        return {"emails": assign_to}
    if isinstance(assign_to, BaseModel):
        return assign_to.model_dump(mode="json")
    return {"value": str(assign_to)}


# ─── Graph-side: await_human ────────────────────────────────────────


async def await_human(
    *,
    task: str,
    payload_schema: type[BaseModel],
    payload: BaseModel,
    response_schema: type[T],
    timeout_seconds: int,
    callback_url: str,
    server_url: str,
    api_key: str | None = None,
    idempotency_key: str | None = None,
    assign_to: object | None = None,
    notify: list[str] | None = None,
    verifier: VerifierConfig | None = None,
    redact_payload: bool = False,
) -> T:
    """Pause a LangGraph node until a human completes a task.

    Parameters mirror the direct-mode `awaithumans.await_human` plus
    LangGraph-specific glue:

      - `callback_url`: the URL on YOUR web server where you mounted
        `dispatch_resume`. Encode the LangGraph `thread_id` in this
        URL so the callback handler can resume the right graph
        instance:
          `https://my-app.com/awaithumans/cb?thread={thread_id}`
      - `server_url`: the awaithumans server (the human-facing one
        running `awaithumans dev` or your hosted deployment).
      - `api_key`: bearer token for `server_url`.

    `interrupt(...)` does double duty: on the FIRST invocation of
    this node it throws `GraphInterrupt` (caller catches at the
    `.ainvoke()` boundary, the checkpointer persists state); on the
    SECOND invocation (resume) the same call returns the value
    passed to `Command(resume=...)`. This function `await`s on that
    return — so semantically it "blocks" until the human submits,
    even though the underlying process may have died and been
    restarted in between.

    Raises `TaskTimeoutError` / `TaskCancelledError` /
    `VerificationExhaustedError` / `SchemaValidationError` on the
    matching server-side terminal status.
    """
    _require_langgraph()

    # Imports deferred so this module is import-safe without
    # langgraph installed — the gate above only fires when this is
    # actually called from inside a graph node.
    from langgraph.types import interrupt

    idem = idempotency_key or _default_idempotency_key(task, payload)

    # Create the task on the server FIRST. The server's per-key
    # uniqueness (active-tasks-only) makes this idempotent across
    # node replays — a checkpoint-restore that re-runs this node
    # will hit the same task without spawning a duplicate ticket
    # for the human.
    #
    # We do this BEFORE interrupt() so the human-facing surface
    # (Slack DM, email, dashboard row) appears immediately. Putting
    # interrupt first would create a "tree falls in the forest"
    # window where the graph is paused but no human has been
    # notified yet.
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Derive form_definition from response_schema so the dashboard
    # can render the Approve / Reject form. Direct-mode SDK does the
    # same in `client.py:159`. Without this, an operator opening the
    # task page sees the payload but no form to submit.
    from awaithumans.forms import extract_form

    form_definition = extract_form(response_schema).model_dump(mode="json")

    body = {
        "task": task,
        "payload": payload.model_dump(mode="json"),
        "payload_schema": payload_schema.model_json_schema(),
        "response_schema": response_schema.model_json_schema(),
        "form_definition": form_definition,
        "timeout_seconds": timeout_seconds,
        "idempotency_key": idem,
        "assign_to": _serialize_assign_to(assign_to),
        "notify": notify,
        "verifier_config": verifier.model_dump() if verifier else None,
        "redact_payload": redact_payload,
        "callback_url": callback_url,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{server_url.rstrip('/')}/api/tasks",
            json=body,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"awaithumans server rejected task creation "
                f"(HTTP {resp.status_code}): {resp.text[:500]}"
            )
        task_record = resp.json()

    logger.info(
        "LangGraph adapter created task task_id=%s idempotency_key=%s",
        task_record.get("id"),
        idem,
    )

    # Idempotency-collision short-circuit. If the server returned an
    # already-terminal task — e.g. the dev re-ran the example with
    # the same idempotency_key after a previous completion — there's
    # no webhook coming and `Command(resume=...)` would never fire.
    # Pre-#72 this hung the graph on `interrupt()` until the
    # checkpointer's timeout (or forever, if there isn't one). Treat
    # this as the Stripe-style retry-returns-cached-response contract:
    # log loudly, then resolve from `task_record["response"]` and
    # skip `interrupt()` entirely. Subsequent nodes run normally.
    existing_status = task_record.get("status")
    if existing_status in _TERMINAL_STATUS_VALUES:
        logger.warning(
            "LangGraph adapter: idempotency_key=%s already exists, status=%s, "
            "completed_at=%s. Returning cached response without calling "
            "interrupt(). Pass a fresh idempotency_key= for a new attempt.",
            idem,
            existing_status,
            task_record.get("completed_at") or task_record.get("timed_out_at"),
        )
        return _resolve_terminal(
            existing_status,
            task_record,
            response_schema,
            task=task,
            timeout_seconds=timeout_seconds,
        )

    # First run: throws GraphInterrupt, graph pauses, caller returns.
    # Resume: returns the dict the callback handler passed to
    # Command(resume=...). We hand the FULL webhook body through (not
    # just `response`) so this function can branch on `status` to
    # surface the right typed error.
    resume_value = interrupt(
        {
            "task_id": task_record.get("id"),
            "idempotency_key": idem,
            "task": task,
            "payload": payload.model_dump(mode="json"),
            "callback_url": callback_url,
        }
    )

    if not isinstance(resume_value, dict):
        raise RuntimeError(
            f"LangGraph adapter expected a dict resume value, got "
            f"{type(resume_value).__name__}. Did your callback handler "
            f"forget to pass the webhook body to Command(resume=...)?"
        )

    status = resume_value.get("status")
    if status == "completed":
        try:
            return response_schema.model_validate(resume_value.get("response"))
        except Exception as exc:  # noqa: BLE001
            raise SchemaValidationError("response", str(exc)) from exc
    if status == "timed_out":
        raise TaskTimeoutError(task=task, timeout_seconds=timeout_seconds)
    if status == "cancelled":
        raise TaskCancelledError(task)
    if status == "verification_exhausted":
        attempt = (
            resume_value.get("verification_attempt", 0) if isinstance(resume_value, dict) else 0
        )
        raise VerificationExhaustedError(task, attempt)
    raise RuntimeError(
        f"LangGraph adapter saw unknown terminal status '{status}' for task '{task}'"
    )


# Terminal status values as wire strings (server returns them as
# strings on the task JSON; no need to import the TaskStatus enum
# just for this set).
_TERMINAL_STATUS_VALUES = frozenset(
    {"completed", "timed_out", "cancelled", "verification_exhausted"}
)


def _resolve_terminal(
    status: str,
    task_record: dict[str, Any],
    response_schema: type[T],
    *,
    task: str,
    timeout_seconds: int,
) -> T:
    """Map a server-returned terminal task to the right return value.

    Used by the idempotency-collision short-circuit. Mirrors the
    post-resume branches in `await_human` but takes the full task
    dict from the server instead of the webhook resume payload —
    only difference is where the `response` field lives."""
    if status == "completed":
        try:
            return response_schema.model_validate(task_record.get("response"))
        except Exception as exc:  # noqa: BLE001
            raise SchemaValidationError("response", str(exc)) from exc
    if status == "timed_out":
        raise TaskTimeoutError(task=task, timeout_seconds=timeout_seconds)
    if status == "cancelled":
        raise TaskCancelledError(task)
    if status == "verification_exhausted":
        raise VerificationExhaustedError(task, task_record.get("verification_attempt", 0))
    raise RuntimeError(
        f"LangGraph adapter saw unknown terminal status '{status}' for task '{task}'"
    )


# ─── User-web-server-side: dispatch_resume ──────────────────────────


# Type alias for the bits of a compiled graph we touch. Kept
# structural (a callable that takes Command + config) so users can
# pass a real `CompiledStateGraph` OR a wrapper without us pulling
# the heavy class.
GraphInvokable = Callable[..., Awaitable[Any]]


async def dispatch_resume(
    *,
    graph: Any,
    thread_id: str,
    body: bytes,
    signature_header: str | None,
) -> dict[str, Any]:
    """Verify a webhook body and resume the matching graph thread.

    The user's web server wraps this in a route handler. They:

      1. Read the thread_id from the request URL (a query param they
         baked into `callback_url=` when constructing the node's
         call to `await_human`).
      2. Pass the raw body bytes and the `X-Awaithumans-Signature`
         header value to this function along with the compiled graph.
      3. Return 200 on success, 401 on `PermissionError`, 400 on
         `ValueError`, 500 on anything else.

    See `examples/langgraph-py/server.py` for a ~10-line FastAPI
    wrapper. The split is deliberate: the SDK doesn't know which web
    framework you use, but it owns the security-critical bits
    (signature verification, payload parsing, Command construction).

    Returns the JSON-decoded webhook payload so the caller can log
    or audit it after the resume completes.
    """
    _require_langgraph()
    from langgraph.types import Command

    # HMAC verification lives in `utils.webhook_signing` so importing
    # it doesn't transitively pull in the [server] extra (FastAPI,
    # SQLModel, etc.). Pre-PR-#71 this import resolved through the
    # server package and a callback receiver running with only
    # [langgraph] installed crashed on the first webhook.
    from awaithumans.utils.webhook_signing import verify_signature

    if not verify_signature(body=body, signature=signature_header):
        raise PermissionError("Invalid awaithumans webhook signature.")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Webhook body is not JSON: {exc}") from exc

    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(Command(resume=payload), config=config)

    logger.info(
        "Resumed graph thread_id=%s task_id=%s status=%s",
        thread_id,
        payload.get("task_id") if isinstance(payload, dict) else None,
        payload.get("status") if isinstance(payload, dict) else None,
    )
    return payload if isinstance(payload, dict) else {}
