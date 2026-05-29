"""Core client — await_human() async and await_human_sync()."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    import httpx  # noqa: F401

from awaithumans.errors import (
    MarketplaceNotAvailableError,
    PollError,
    SchemaValidationError,
    ServerUnreachableError,
    TaskCancelledError,
    TaskCreateError,
    TaskNotFoundError,
    TaskTimeoutError,
    TimeoutRangeError,
    VerificationExhaustedError,
)
from awaithumans.forms import extract_form
from awaithumans.types import MarketplaceAssignment, VerifierConfig
from awaithumans.utils.constants import (
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    SDK_CREATE_TIMEOUT_SECONDS,
    SDK_POLL_TIMEOUT_BUFFER_SECONDS,
)
from awaithumans.utils.discovery import resolve_admin_token, resolve_server_url

logger = logging.getLogger("awaithumans.client")

T = TypeVar("T", bound=BaseModel)


def _auth_headers(api_key: str | None) -> dict[str, str]:
    """Build the request headers for API calls.

    The server's auth middleware (added in PR A3) gates every /api/*
    route except a fixed public-prefix list. Task endpoints aren't
    in that list — they require either a session cookie (dashboard
    users) or an admin bearer token (automation, which is us).

    `api_key` comes from `resolve_admin_token()`: explicit arg →
    env var → discovery file → None. When None, skip the header
    entirely and let the server produce a proper 401 the SDK can
    surface as a config error.
    """
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _print_waiting_banner(*, base_url: str, task_id: str, timeout_seconds: int) -> None:
    """Tell the user their script is now blocking on a human.

    Uses `print(..., file=sys.stderr)` directly rather than the logger:
    a plain `python refund.py` has no logging handlers configured, so
    `logger.info` messages vanish and the script looks frozen to the
    operator. stderr keeps stdout clean for anyone piping the return
    value downstream.

    The dashboard URL points at `/task?id=…`, the task-detail route.
    Pre-0.1.1 code pointed at `/api/tasks/{id}` (the raw JSON
    endpoint) — that confused early testers who clicked the link
    expecting the review form.
    """
    dashboard_url = f"{base_url}/task?id={task_id}"
    # CLAUDE.md "no print()" applies to server code; here we're in
    # the SDK that runs inside the user's agent process. Their
    # script almost never has a logging handler attached, so
    # `logger.info` would silently vanish and the script looks
    # frozen. Stderr keeps stdout clean for piping the return value
    # downstream. T201 ruff warning suppressed deliberately.
    print(f"\n✓ Task created: {task_id}", file=sys.stderr)  # noqa: T201
    print(f"  Review at: {dashboard_url}", file=sys.stderr)  # noqa: T201
    print(  # noqa: T201
        f"  Waiting for human (timeout: {timeout_seconds}s). Ctrl-C to abort.",
        file=sys.stderr,
        flush=True,
    )


async def await_human(
    *,
    task: str,
    payload_schema: type[T],
    payload: BaseModel,
    response_schema: type[T],
    timeout_seconds: int,
    assign_to: object | None = None,
    notify: list[str] | None = None,
    verifier: VerifierConfig | None = None,
    idempotency_key: str | None = None,
    redact_payload: bool = False,
    task_metadata: dict[str, str] | None = None,
    server_url: str | None = None,
    api_key: str | None = None,
) -> T:
    """
    Delegate a task to a human and await the result (async).

    Direct mode: creates a task on the server, then long-polls until the
    human completes it or the timeout expires.

    Resumable: same `idempotency_key` returns the same task forever. If
    the agent crashes mid-await and the human completes during the
    outage, re-invoking with the same key returns the stored response
    (your `if decision.approved:` block runs as if nothing happened).
    To start a fresh task for the same event after a previous one
    timed out / was cancelled / was completed, use a distinct key
    (e.g. f"{base}:retry-1"). See docs/concepts/idempotency.

    For durable mode, use awaithumans.temporal or awaithumans.langgraph instead.
    """
    # ── Validate timeout range ───────────────────────────────────────
    if timeout_seconds < MIN_TIMEOUT_SECONDS or timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise TimeoutRangeError(timeout_seconds)

    # ── Validate payload against schema ──────────────────────────────
    try:
        payload_schema.model_validate(payload.model_dump())
    except Exception as e:
        raise SchemaValidationError("payload", str(e)) from e

    # ── Check for reserved marketplace ───────────────────────────────
    if isinstance(assign_to, MarketplaceAssignment):
        raise MarketplaceNotAvailableError()

    # ── Generate idempotency key ─────────────────────────────────────
    key = idempotency_key or _generate_idempotency_key(task, payload)

    # ── Resolve server URL + admin token ────────────────────────────
    base_url = resolve_server_url(explicit_url=server_url)
    resolved_token = resolve_admin_token(explicit_token=api_key)
    auth_headers = _auth_headers(resolved_token)

    # ── Serialize assign_to for the wire ─────────────────────────────
    assign_to_dict = None
    if assign_to is not None:
        if isinstance(assign_to, str):
            assign_to_dict = {"email": assign_to}
        elif isinstance(assign_to, list):
            assign_to_dict = {"emails": assign_to}
        elif isinstance(assign_to, BaseModel):
            assign_to_dict = assign_to.model_dump()
        else:
            assign_to_dict = {"value": str(assign_to)}

    # ── Extract form definition from response schema ────────────────
    # Primitives attached via Annotated win; unannotated fields fall back
    # to type-based inference. Safe for any Pydantic BaseModel.
    form_definition = extract_form(response_schema).model_dump(mode="json")

    # ── Create task on the server ────────────────────────────────────
    # Deferred so `awaithumans` is import-safe inside a Temporal workflow
    # sandbox; httpx transitively pulls in urllib.request, which the
    # sandbox forbids at workflow replay time.
    import httpx

    async with httpx.AsyncClient(timeout=SDK_CREATE_TIMEOUT_SECONDS) as client:
        create_body = {
            "task": task,
            "payload": payload.model_dump(mode="json"),
            "payload_schema": payload_schema.model_json_schema(),
            "response_schema": response_schema.model_json_schema(),
            "form_definition": form_definition,
            "timeout_seconds": timeout_seconds,
            "idempotency_key": key,
            "assign_to": assign_to_dict,
            "notify": notify,
            "verifier_config": verifier.model_dump() if verifier else None,
            "redact_payload": redact_payload,
            "task_metadata": task_metadata,
        }

        try:
            resp = await client.post(
                f"{base_url}/api/tasks",
                json=create_body,
                headers=auth_headers,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Connection-level failure → typed `ServerUnreachableError`
            # so users get a concrete "the server isn't running" hint
            # instead of an opaque httpx exception.
            raise ServerUnreachableError(base_url, exc) from exc
        if resp.status_code not in (200, 201):
            raise TaskCreateError(resp.status_code, resp.text)

        task_data = resp.json()
        task_id = task_data["id"]
        _print_waiting_banner(base_url=base_url, task_id=task_id, timeout_seconds=timeout_seconds)

    # ── Long-poll until completion or timeout ────────────────────────
    result = await _poll_until_terminal(
        base_url, task_id, task, timeout_seconds, response_schema, auth_headers
    )
    return result


async def _poll_until_terminal(
    base_url: str,
    task_id: str,
    task_description: str,
    timeout_seconds: int,
    response_schema: type[T],
    auth_headers: dict[str, str],
) -> T:
    """Long-poll the server until the task reaches a terminal state.

    Reconnects every POLL_INTERVAL_SECONDS seconds to stay under gateway timeouts.
    The server's poll endpoint holds the connection for up to 25 seconds
    per request.
    """
    import httpx

    async with httpx.AsyncClient(
        timeout=POLL_INTERVAL_SECONDS + SDK_POLL_TIMEOUT_BUFFER_SECONDS,
    ) as client:
        while True:
            resp = await client.get(
                f"{base_url}/api/tasks/{task_id}/poll",
                params={"timeout": POLL_INTERVAL_SECONDS},
                headers=auth_headers,
            )

            if resp.status_code == 404:
                raise TaskNotFoundError(task_id)

            if resp.status_code != 200:
                raise PollError(task_id, resp.status_code, resp.text)

            poll_data = resp.json()
            status = poll_data["status"]

            if status == "completed":
                raw_response = poll_data["response"]
                logger.info("Task %s completed", task_id)
                # Validate response against schema and return typed result
                try:
                    return response_schema.model_validate(raw_response)
                except Exception as e:
                    raise SchemaValidationError("response", str(e)) from e

            if status == "timed_out":
                raise TaskTimeoutError(task_description, timeout_seconds)

            if status == "cancelled":
                raise TaskCancelledError(task_description)

            if status == "verification_exhausted":
                raise VerificationExhaustedError(
                    task_description,
                    poll_data.get("verification_attempt", 0),
                )

            # Non-terminal status — the server's poll timed out (25s).
            # Reconnect and poll again.
            logger.debug("Task %s still pending (status=%s), reconnecting...", task_id, status)


def await_human_sync(
    *,
    task: str,
    payload_schema: type[T],
    payload: BaseModel,
    response_schema: type[T],
    timeout_seconds: int,
    assign_to: object | None = None,
    notify: list[str] | None = None,
    verifier: VerifierConfig | None = None,
    idempotency_key: str | None = None,
    redact_payload: bool = False,
    server_url: str | None = None,
    api_key: str | None = None,
) -> T:
    """
    Delegate a task to a human and block until the result (sync).

    Convenience wrapper for sync code (Flask, Celery, LangChain pre-v0.3).
    Runs await_human() in a new event loop.
    """
    return asyncio.run(
        await_human(
            task=task,
            payload_schema=payload_schema,
            payload=payload,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            assign_to=assign_to,
            notify=notify,
            verifier=verifier,
            idempotency_key=idempotency_key,
            redact_payload=redact_payload,
            server_url=server_url,
            api_key=api_key,
        )
    )


def _generate_idempotency_key(task: str, payload: BaseModel) -> str:
    """Generate a deterministic key from task + payload using canonical JSON."""
    canonical = json.dumps(
        {"task": task, "payload": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]
