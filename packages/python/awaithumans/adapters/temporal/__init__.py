"""Temporal adapter — signal-based durable HITL.

Two halves, deployed in two different processes:

  1. **Inside a Temporal workflow** — the agent calls `await_human(...)`
     from this module. We register a signal handler scoped to the
     task's idempotency key, fire an activity that POSTs the task to
     the awaithumans server, then `workflow.wait_condition` blocks on
     either the signal arriving OR a workflow.sleep timeout — both
     cost zero compute under Temporal's "park the workflow" model.

  2. **Inside the user's web server** — `create_callback_handler` is
     mounted at a public URL whose path you give to await_human() via
     `callback_url=`. The awaithumans server POSTs there on terminal
     transitions; the handler verifies the HMAC, extracts the workflow
     identity from the idempotency key, and signals the workflow back
     to life.

Wire diagram:

    workflow                 awaithumans server          user web server
    ────────                 ──────────────────          ───────────────
    register signal handler
    ──── activity: ──►       POST /api/tasks
                             store with callback_url
                             return task_id
    wait_condition(...)      ── human completes ──►
                             POST callback_url ──►      verify HMAC
                                                        signal workflow
    ◄── signal received ─────────────────────────────────
    return response

Idempotency: the agent passes (or we derive) an `idempotency_key`. We
use that same key as both the server's dedup gate AND our signal
name. Replays of the workflow produce the same key → same task →
same signal name. Two concurrent `await_human` calls in one workflow
must use distinct keys (default: hash(task, payload), so different
payloads give different keys); pass `idempotency_key=` explicitly to
disambiguate if you call with the same args twice.

Requires: `pip install "awaithumans[temporal]"`. The whole module is
import-safe without temporalio installed; the call site fails-fast
with a clear ImportError when actually invoked."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
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

logger = logging.getLogger("awaithumans.adapters.temporal")

T = TypeVar("T", bound=BaseModel)

# Signal-name prefix. Keep it stable — the server-side handler in the
# user's web server depends on this exact prefix to route the signal.
_SIGNAL_PREFIX = "awaithumans"

# Default activity timeout for the create-task POST. The server
# handler is fast (DB write + BackgroundTask schedule); 30s is
# generous for slow networks. Configurable per-call via the
# `create_activity_timeout_seconds` parameter.
_DEFAULT_CREATE_ACTIVITY_TIMEOUT_SECONDS = 30


def _require_temporal() -> None:
    """Lazy import gate — temporalio is an optional extra."""
    try:
        import temporalio  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The Temporal adapter requires the [temporal] extra.\n"
            'Install with: pip install "awaithumans[temporal]"'
        ) from exc


# ─── Activity (runs OUTSIDE the workflow sandbox) ────────────────────


@dataclass(frozen=True)
class _CreateTaskInput:
    """Wire-friendly args for the create-task activity.

    Pydantic models can't cross the workflow/activity boundary
    cleanly without converters; a frozen dataclass of plain types is
    Temporal's preferred shape. We serialize Pydantic schemas to
    JSON-Schema dicts on the workflow side before invoking."""

    server_url: str
    api_key: str | None
    task: str
    payload: dict[str, Any]
    payload_schema: dict[str, Any]
    response_schema: dict[str, Any]
    form_definition: dict[str, Any] | None
    timeout_seconds: int
    idempotency_key: str
    callback_url: str
    assign_to: dict[str, Any] | None
    notify: list[str] | None
    verifier_config: dict[str, Any] | None
    redact_payload: bool


def _activity_defn() -> Any:
    """Lazy-bind `@activity.defn` so this module imports cleanly even
    when temporalio isn't installed. The decorator is applied on the
    `awaithumans_create_task` function below; without it, the worker
    rejects the activity at registration time with "missing attributes,
    was it decorated with @activity.defn?". A naked import-time
    `from temporalio import activity` would force every consumer of
    this module — including the direct-mode SDK — to install the
    [temporal] extra, which we explicitly avoid."""
    try:
        from temporalio import activity

        return activity.defn
    except ImportError:
        # If temporalio isn't installed we never reach this code from
        # a worker (the import gate in `_require_temporal` fires
        # first), but the decorator still has to be importable at
        # module load. A no-op stand-in keeps the module loadable;
        # the worker registration would fail later with the same
        # "missing attributes" error if anyone tried to use it
        # without installing the extra.
        return lambda fn: fn


@_activity_defn()
async def awaithumans_create_task(req: _CreateTaskInput) -> dict[str, Any]:
    """Activity: POST the task to the awaithumans server.

    Register on your Temporal worker alongside your own activities:

        from awaithumans.adapters.temporal import awaithumans_create_task

        async with Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[YourWorkflow],
            activities=[your_activity, awaithumans_create_task],
        ):
            ...

    Lives in the user's worker process, NOT inside the workflow
    sandbox — HTTP and most stdlib I/O is forbidden in workflow code.
    Temporal's automatic activity retries cover transient server
    errors; the workflow's wait_condition only proceeds once the
    activity returns a task_id."""
    headers: dict[str, str] = {}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"

    body = {
        "task": req.task,
        "payload": req.payload,
        "payload_schema": req.payload_schema,
        "response_schema": req.response_schema,
        "form_definition": req.form_definition,
        "timeout_seconds": req.timeout_seconds,
        "idempotency_key": req.idempotency_key,
        "assign_to": req.assign_to,
        "notify": req.notify,
        "verifier_config": req.verifier_config,
        "redact_payload": req.redact_payload,
        "callback_url": req.callback_url,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{req.server_url.rstrip('/')}/api/tasks",
            json=body,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"awaithumans server rejected task creation "
                f"(HTTP {resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()


# ─── Workflow-side: await_human ──────────────────────────────────────


def _signal_name(idempotency_key: str) -> str:
    return f"{_SIGNAL_PREFIX}:{idempotency_key}"


async def await_human(
    *,
    task: str,
    payload_schema: type[T],
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
    create_activity_timeout_seconds: int = _DEFAULT_CREATE_ACTIVITY_TIMEOUT_SECONDS,
) -> T:
    """Suspend a Temporal workflow until a human completes a task.

    Parameters mirror the direct-mode `awaithumans.await_human` plus
    Temporal-specific glue:

      - `callback_url`: the URL on YOUR web server where you mounted
        `create_callback_handler`. The awaithumans server POSTs here
        when the human finishes.
      - `server_url`: the awaithumans server (the human-facing one
        running `awaithumans dev` or your hosted deployment).
      - `api_key`: bearer token for `server_url`. Same value the
        non-Temporal SDK reads.

    Re-running this exact `await_human` call inside the same workflow
    (after a replay or a worker restart) hits the SAME server task
    via idempotency — the human only sees one ticket, the workflow
    only signals once.

    Raises `TaskTimeoutError` if the workflow's wait exceeds
    `timeout_seconds` (which matches the server's own timeout
    scheduler — usually they fire near-simultaneously and the
    workflow gets the timeout signal too)."""
    _require_temporal()

    # Imports deferred so this module is loadable without
    # temporalio installed — the import gate above only fires when
    # await_human is actually called from a workflow.
    import asyncio

    from temporalio import workflow

    info = workflow.info()
    # Default key = workflow_id (matches the TS adapter, PR #60).
    # Stable across replays of the same run AND unique per kickoff
    # because the kickoff generates `refund-{uuid.uuid4()}`. Content-
    # hash dedup is still available — pass `idempotency_key=` if you
    # want it (e.g. `f"refund:{order_id}"`). The pre-#72 default was
    # `temporal:{hash(task,payload)}` which collided across runs with
    # identical content and made the example unrunnable on retry —
    # see #72 for the failure mode.
    idem = idempotency_key or f"temporal:{info.workflow_id}"
    signal = _signal_name(idem)

    # Captured by the signal handler closure. We can't use a plain
    # variable because Python closures don't let inner functions
    # rebind outer scalars; a 1-element list dodges that without
    # introducing a class.
    received: list[Any] = [None]
    completed_status: list[str | None] = [None]

    def _on_signal(arg: dict[str, Any] | None) -> None:
        # The server-side handler in the user's web server signals
        # with the full webhook payload; we keep both the response
        # AND the terminal status so we can raise the right typed
        # error when the human didn't complete (cancelled,
        # exhausted).
        if not isinstance(arg, dict):
            logger.warning(
                "Temporal adapter got non-dict signal payload for %s",
                signal,
            )
            return
        completed_status[0] = arg.get("status")
        received[0] = arg.get("response")

    workflow.set_signal_handler(signal, _on_signal)
    logger.info(
        "Temporal adapter registered signal handler workflow_id=%s signal=%s",
        info.workflow_id,
        signal,
    )

    # Serialize Pydantic types to JSON-Schema dicts BEFORE the
    # activity boundary — Pydantic classes don't survive Temporal's
    # data-converter without custom plumbing.
    payload_dict = payload.model_dump(mode="json")
    assign_to_dict: dict[str, Any] | None
    if assign_to is None:
        assign_to_dict = None
    elif isinstance(assign_to, str):
        assign_to_dict = {"email": assign_to}
    elif isinstance(assign_to, list):
        assign_to_dict = {"emails": assign_to}
    elif isinstance(assign_to, BaseModel):
        assign_to_dict = assign_to.model_dump(mode="json")
    else:
        assign_to_dict = {"value": str(assign_to)}

    # Derive form_definition from response_schema so the dashboard can
    # render the Approve / Reject form when an operator claims the
    # task. Direct-mode SDK does the same in `client.py:159`. Without
    # this, dashboard-driven approval has nothing to render.
    #
    # Imported INSIDE the workflow function (with the lazy
    # `imports_passed_through` already in effect) so the heavy
    # `awaithumans.forms` module isn't loaded at every workflow
    # replay's first activation — only the once-per-await-human path.
    from awaithumans.forms import extract_form

    form_definition = extract_form(response_schema).model_dump(mode="json")

    create_input = _CreateTaskInput(
        server_url=server_url,
        api_key=api_key,
        task=task,
        payload=payload_dict,
        payload_schema=payload_schema.model_json_schema(),
        response_schema=response_schema.model_json_schema(),
        form_definition=form_definition,
        timeout_seconds=timeout_seconds,
        idempotency_key=idem,
        callback_url=callback_url,
        assign_to=assign_to_dict,
        notify=notify,
        verifier_config=verifier.model_dump() if verifier else None,
        redact_payload=redact_payload,
    )

    task_record = await workflow.execute_activity(
        awaithumans_create_task,
        create_input,
        start_to_close_timeout=timedelta(seconds=create_activity_timeout_seconds),
    )

    # Idempotency-collision short-circuit. If the server returned an
    # already-terminal task — e.g. the dev re-ran the example with the
    # same idempotency_key after a previous completion — there's no
    # signal coming. Pre-#72 the workflow parked on wait_condition for
    # the full `timeout_seconds` and then raised TaskTimeoutError, a
    # 15-minute wait for what should be an immediate cached return.
    # Treat this as the Stripe-style retry-returns-cached-response
    # contract: log loudly, then resolve from `task_record["response"]`.
    existing_status = task_record.get("status") if isinstance(task_record, dict) else None
    if existing_status in _TERMINAL_STATUS_VALUES:
        logger.warning(
            "Temporal adapter: idempotency_key=%s already exists, status=%s, "
            "completed_at=%s. Returning cached response without waiting for "
            "a new signal. Pass a fresh idempotency_key= for a new attempt.",
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

    # Wait for the signal OR the timeout — Temporal parks the
    # workflow under both, no compute consumed while idle. asyncio's
    # TimeoutError surfaces at the workflow level when the timer
    # fires first.
    try:
        await workflow.wait_condition(
            lambda: completed_status[0] is not None,
            timeout=timedelta(seconds=timeout_seconds),
        )
    except asyncio.TimeoutError as exc:
        raise TaskTimeoutError(task=task, timeout_seconds=timeout_seconds) from exc

    status = completed_status[0]
    if status == "completed":
        try:
            return response_schema.model_validate(received[0])
        except Exception as exc:  # noqa: BLE001
            raise SchemaValidationError("response", str(exc)) from exc
    if status == "timed_out":
        raise TaskTimeoutError(task=task, timeout_seconds=timeout_seconds)
    if status == "cancelled":
        raise TaskCancelledError(task)
    if status == "verification_exhausted":
        # The webhook payload doesn't carry max_attempts; the
        # server's verification_attempt counter at terminal state is
        # the closest signal of how many tries the human got.
        attempt = received[0].get("verification_attempt", 0) if isinstance(received[0], dict) else 0
        raise VerificationExhaustedError(task, attempt)
    # Unknown status — shouldn't happen, but fail loud rather than silent.
    raise RuntimeError(f"Temporal adapter saw unknown terminal status '{status}' for task '{task}'")


# Terminal status values as wire strings (the server sends `task["status"]`
# as a string; we don't need to import the TaskStatus enum for this set).
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

    Used by the idempotency-collision short-circuit AND by the
    LangGraph adapter (PR #72). Mirrors the post-signal branches at
    the bottom of `await_human` but takes the full task dict from
    the server instead of the webhook payload — only difference is
    where the `response` field lives."""
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
    raise RuntimeError(f"Temporal adapter saw unknown terminal status '{status}' for task '{task}'")


# ─── User-web-server-side: dispatch_signal ──────────────────────────


async def dispatch_signal(
    *,
    temporal_client: Any,
    workflow_id: str,
    body: bytes,
    signature_header: str | None,
) -> None:
    """Verify a callback body and signal the matching workflow.

    The user's web server wraps this in a route handler. They:

      1. Read the workflow_id from the request URL (a query param
         they bake into `callback_url=` when constructing the
         workflow's call to `await_human`).
      2. Pass the raw body bytes and the
         `X-Awaithumans-Signature` header value to this function.
      3. Return 200 on success, 401 on `PermissionError`, 400 on
         `ValueError`.

    See `examples/temporal/server.py` for a ~10-line FastAPI
    wrapper. The split is deliberate: the SDK doesn't know what web
    framework you use, but it owns the security-critical bits
    (signature verification, signal-name derivation, payload parsing).

    `temporal_client` is whatever you got back from
    `await temporalio.client.Client.connect(...)` at startup —
    typically a long-lived module-level singleton."""
    # HMAC verification lives in `utils.webhook_signing` so importing
    # it doesn't transitively pull in the [server] extra (FastAPI,
    # SQLModel, etc.). Pre-PR-#71 this import resolved through the
    # server package and the callback receiver crashed with
    # `ModuleNotFoundError: cryptography` when only [temporal] was
    # installed.
    from awaithumans.utils.webhook_signing import verify_signature

    if not verify_signature(body=body, signature=signature_header):
        # PermissionError is the right shape — the wrapper renders
        # 401. This is a security event, not a payload bug.
        raise PermissionError("Invalid awaithumans webhook signature.")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Webhook body is not JSON: {exc}") from exc

    idem = payload.get("idempotency_key")
    if not isinstance(idem, str):
        raise ValueError(f"Webhook missing idempotency_key: {payload!r}")

    signal = _signal_name(idem)
    handle = temporal_client.get_workflow_handle(workflow_id)
    await handle.signal(signal, payload)
    logger.info(
        "Signalled workflow_id=%s signal=%s task_id=%s status=%s",
        workflow_id,
        signal,
        payload.get("task_id"),
        payload.get("status"),
    )
