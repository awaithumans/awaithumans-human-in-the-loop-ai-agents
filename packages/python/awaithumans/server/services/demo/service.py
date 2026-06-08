"""Top-level demo orchestrator.

Sequences Turnstile verify, email gate, rate caps, schema spec
resolution, AI extraction, confidence-driven flagging, and the
DemoRecord insert.

The downstream AwaitVerify task creation runs as a background task.
The orchestrator returns to the wizard immediately with the AI result
so it can render the confirmed fields. The reviewer is then routed via
the standard AwaitVerify pipeline using ``priority=demo`` (or
``demo_hot`` for warm prospects).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.channels.email.demo_email import (
    send_demo_review_complete_email,
)
from awaithumans.server.core.config import settings
from awaithumans.server.db.models import DemoRecord, DemoStatus
from awaithumans.server.services.demo.email_gate import validate_demo_email
from awaithumans.server.services.demo.exceptions import (
    DemoRecordNotFoundError,
    DemoSchemaError,
)
from awaithumans.server.services.demo.extractor import (
    ExtractionResult,
    run_demo_extraction,
)
from awaithumans.server.services.demo.rate_limit import (
    DemoCaps,
    check_demo_caps,
    estimate_provider_cost_cents,
)
from awaithumans.server.services.demo.schema_builder import (
    build_pydantic_model,
    spec_from_json,
)
from awaithumans.server.services.demo.schema_proposer import (
    propose_schema_from_page,
)
from awaithumans.server.services.demo.turnstile import verify_turnstile_token

logger = logging.getLogger("awaithumans.server.services.demo.service")


@dataclass
class StartDemoInput:
    email: str
    ip_hash: str
    # Optional: when None the orchestrator auto-proposes a schema from
    # the page via the LLM before extraction. The wizard collapsed to
    # 3 steps and never sends a spec; explicit specs are still accepted
    # for service-level tests and any future caller that wants control.
    schema_spec: dict[str, Any] | None = None
    is_hot_demo: bool = False
    turnstile_token: str = ""
    page_png: bytes = b""


@dataclass
class StartDemoOutput:
    demo_id: str
    ai_result: dict[str, Any]
    pending_field_names: list[str]


BackgroundRunner = Callable[..., Awaitable[Any]]


async def start_demo(
    session: AsyncSession,
    *,
    input_: StartDemoInput,
    background_runner: BackgroundRunner | None = None,
) -> StartDemoOutput:
    caps = _caps_from_settings()
    allowlist = _allowlist_from_settings()

    # Email validation is cheap and deterministic; run it before the
    # Turnstile network call so obviously-bad emails fail fast without
    # spending a Cloudflare round trip.
    validate_demo_email(input_.email, allowlist_extra=allowlist)

    await verify_turnstile_token(
        token=input_.turnstile_token,
        remote_ip=None,
        secret=settings.TURNSTILE_SECRET or "",
    )

    per_call_cost = estimate_provider_cost_cents()
    await check_demo_caps(
        session,
        email=input_.email,
        ip_hash=input_.ip_hash,
        per_call_cost_cents=per_call_cost,
        caps=caps,
    )

    # The collapsed 3-step wizard never sends a schema spec; the
    # orchestrator auto-proposes one from the page image via the same
    # LLM that powers the extractor. Service-level tests still pass a
    # spec explicitly to exercise the deterministic path.
    if input_.schema_spec is None:
        proposed_spec = await propose_schema_from_page(input_.page_png)
        input_.schema_spec = proposed_spec.model_dump()

    # Resolve the visitor's schema spec to a concrete Pydantic model.
    # `spec_from_json` raises DemoSchemaError on shape / identifier /
    # type-allowlist violations; `build_pydantic_model` raises the same
    # on structural problems. The route layer maps DemoSchemaError to a
    # 400 via the centralized exception handler.
    spec = spec_from_json(input_.schema_spec)
    response_model = build_pydantic_model(spec)
    schema_spec_persisted = spec.model_dump()

    extraction: ExtractionResult = await run_demo_extraction(
        page_png=input_.page_png,
        response_model=response_model,
    )

    threshold = settings.DEMO_CONFIDENCE_THRESHOLD
    pending = sorted(
        name for name, conf in extraction.confidences.items() if conf < threshold
    )

    # Demo floor: even when the LLM is uniformly confident, force the
    # lowest-confidence fields into pending so the visitor always sees
    # the human-review moment. Tunable via DEMO_MIN_FLAGGED_RATIO; a
    # ratio of 0 disables the floor entirely.
    total_fields = len(extraction.confidences)
    ratio = settings.DEMO_MIN_FLAGGED_RATIO
    if total_fields > 0 and ratio > 0:
        target = max(1, int(round(total_fields * ratio)))
        if len(pending) < target:
            ranked = sorted(
                (name for name in extraction.confidences if name not in pending),
                key=lambda n: extraction.confidences[n],
            )
            for name in ranked:
                if len(pending) >= target:
                    break
                pending.append(name)
            pending = sorted(set(pending))

    record = DemoRecord(
        email=input_.email,
        email_domain=input_.email.rsplit("@", 1)[1].lower(),
        ip_hash=input_.ip_hash,
        is_hot_demo=input_.is_hot_demo,
        schema_spec=schema_spec_persisted,
        ai_result=extraction.values,
        field_confidences=extraction.confidences,
        pending_field_names=pending,
        cost_estimate_cents=extraction.cost_cents,
        status=DemoStatus.ROUTING,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    if background_runner is not None and pending:
        await background_runner(demo_id=record.id, page_png=input_.page_png)

    return StartDemoOutput(
        demo_id=record.id,
        ai_result=extraction.values,
        pending_field_names=pending,
    )


def _caps_from_settings() -> DemoCaps:
    return DemoCaps(
        per_email_window_days=settings.DEMO_PER_EMAIL_WINDOW_DAYS,
        per_ip_window_hours=settings.DEMO_PER_IP_WINDOW_HOURS,
        per_ip_cap=settings.DEMO_PER_IP_CAP,
        daily_cap=settings.DEMO_DAILY_CAP,
        daily_cost_ceiling_cents=settings.DEMO_DAILY_COST_CEILING_CENTS,
    )


def _allowlist_from_settings() -> frozenset[str]:
    raw = settings.DEMO_EMAIL_ALLOWLIST_EXTRA.strip()
    if not raw:
        return frozenset()
    return frozenset(domain.strip().lower() for domain in raw.split(",") if domain.strip())


# ─── Per-field reviewer submit ──────────────────────────────────────────


@dataclass
class SubmitFieldResult:
    """Return shape from ``submit_demo_field``.

    The route layer flattens this into the public JSON response. Status
    is the new ``DemoStatus`` after this submission; ``pending_field_names``
    is what's still outstanding (empty when the last field just landed).
    """

    demo_id: str
    status: DemoStatus
    pending_field_names: list[str]
    field_corrections: dict[str, Any]


async def submit_demo_field(
    session: AsyncSession,
    *,
    demo_id: str,
    field_name: str,
    value: Any,
    reviewer_email: str | None,
) -> SubmitFieldResult:
    """Apply a single reviewer correction to a demo record.

    Drives the v2 hero moment: each field the reviewer submits gets
    pushed back to the visitor's browser via the polling endpoint, and
    when the last pending field lands the receipt email fires.

    ``reviewer_email`` is None when called from service-level tests
    (the route layer always passes the authenticated reviewer). When
    set AND the demo has a linked AwaitVerify task, we confirm the
    submitter is the assigned reviewer; mismatches surface as
    ``DemoSchemaError`` so the route returns 4xx without leaking which
    field belongs to which reviewer.

    The receipt email is fired with ``asyncio.create_task`` so the
    final submission still returns immediately. The visitor sees the
    last corrected field render in their browser without waiting for
    SMTP. The completion of the linked AwaitVerify task happens
    in-line because the agent that owns the task needs the terminal
    transition committed before the route returns.
    """
    record = await session.get(DemoRecord, demo_id)
    if record is None:
        raise DemoRecordNotFoundError(demo_id)

    if field_name not in record.pending_field_names:
        raise DemoSchemaError(f"Field {field_name!r} is not pending for demo {demo_id}.")

    # Authorisation: confirm the signed-in reviewer is the assignee on
    # the linked AwaitVerify task. Deny by default: a missing task link,
    # a vanished task, or a mismatched assignee all reject the submit.
    # ``reviewer_email`` is None only for the admin-bearer path (callers
    # that have already proved they own the entire API surface) and for
    # service-level tests that aren't exercising the auth layer.
    if reviewer_email is not None:
        if record.awaitverify_task_id is None:
            raise DemoSchemaError("Not authorized to submit this field.")
        from awaithumans.server.db.models import Task

        task = await session.get(Task, record.awaitverify_task_id)
        if task is None:
            raise DemoSchemaError("Not authorized to submit this field.")
        assigned_email = (task.assigned_to_email or "").strip().lower()
        if not assigned_email or assigned_email != reviewer_email.strip().lower():
            raise DemoSchemaError("Not authorized to submit this field.")

    new_corrections: dict[str, Any] = dict(record.field_corrections or {})
    new_corrections[field_name] = value
    record.field_corrections = new_corrections

    new_pending = [n for n in record.pending_field_names if n != field_name]
    record.pending_field_names = new_pending

    if not new_pending:
        record.status = DemoStatus.REVIEW_COMPLETE
    else:
        record.status = DemoStatus.PARTIALLY_DONE

    session.add(record)
    await session.commit()
    await session.refresh(record)

    # When the last flagged field lands, complete the linked
    # AwaitVerify task and fire the receipt email out-of-band so the
    # route returns immediately. The visitor's browser sees the final
    # correction without waiting on SMTP or the verifier path.
    if not new_pending:
        if record.awaitverify_task_id is not None:
            await _complete_linked_task(
                session,
                task_id=record.awaitverify_task_id,
                response=new_corrections,
                reviewer_email=reviewer_email,
            )
        asyncio.create_task(send_demo_review_complete_email(record))

    return SubmitFieldResult(
        demo_id=record.id,
        status=record.status,
        pending_field_names=new_pending,
        field_corrections=new_corrections,
    )


async def _complete_linked_task(
    session: AsyncSession,
    *,
    task_id: str,
    response: dict[str, Any],
    reviewer_email: str | None,
) -> None:
    """Mark the linked AwaitVerify task as completed.

    Best-effort: a failure here is logged and swallowed so the visitor
    still sees the final field correction in their browser. The
    underlying reviewer corrections are already committed on the
    ``DemoRecord`` so the audit trail isn't lost.
    """
    from awaithumans.server.services.exceptions import TaskAlreadyTerminalError
    from awaithumans.server.services.task_service import complete_task

    try:
        await complete_task(
            session,
            task_id=task_id,
            response=response,
            completed_by_email=reviewer_email,
            completed_via_channel="demo",
        )
    except TaskAlreadyTerminalError:
        # The task was already completed via the standard dashboard
        # path; the demo flow just caught up. Nothing to do.
        logger.info("Linked AwaitVerify task %s already terminal", task_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to complete linked AwaitVerify task %s: %s", task_id, exc)


# ─── AwaitVerify task creation (background runner) ──────────────────────


async def _create_awaitverify_task(*, demo_id: str, page_png: bytes) -> None:
    """Background runner: create the AwaitVerify task for a demo.

    Runs OUTSIDE the request lifecycle (kicked off by
    ``BackgroundTasks.add_task`` in the route layer). Acquires its own
    short-lived session so the orchestrator's request session can be
    closed immediately after returning the AI result to the wizard.

    On success, writes ``awaitverify_task_id`` back onto the
    ``DemoRecord`` and flips status to ``awaiting_claim``. On any
    failure, marks the row ``routing_failed`` so the polling endpoint
    can surface the fallback state to the wizard. The actual fallback
    email send lives in Task 15.
    """
    from awaithumans.awaitverify.types import ManagedAssignment, Priority
    from awaithumans.server.db.connection import get_async_session_factory
    from awaithumans.server.services.task_service import create_task

    factory = get_async_session_factory()
    async with factory() as session:
        record = await session.get(DemoRecord, demo_id)
        if record is None:
            logger.error("Demo %s missing in background runner", demo_id)
            return

        try:
            priority = Priority.DEMO_HOT if record.is_hot_demo else Priority.DEMO
            assign_to = ManagedAssignment(priority=priority).model_dump()

            ai_values = record.ai_result or {}
            pending_names = list(record.pending_field_names)
            schema_name = record.schema_name

            # Response schema: only the flagged fields are corrected by
            # the reviewer. High-confidence fields render confirmed in
            # the wizard immediately and never round-trip.
            response_schema: dict[str, Any] = {
                "type": "object",
                "properties": {name: {"type": "string"} for name in pending_names},
            }

            # The reviewer needs the original page to verify against.
            # Embed it base64 in the payload so the dashboard's existing
            # task view can render it without a separate attachment
            # store. TODO(task-34): replace with a dashboard demo-mode
            # surface that fetches the bytes from a dedicated demo
            # asset endpoint instead of inlining megabytes per task.
            page_b64 = base64.b64encode(page_png).decode("ascii")

            payload = {
                "demo_id": demo_id,
                "schema_spec": record.schema_spec,
                "schema_name": schema_name,
                "ai_values": ai_values,
                "ai_confidences": record.field_confidences,
                "pending_field_names": pending_names,
                "page_image_base64": page_b64,
            }

            # Hot-lane demos (warm prospects) get an URGENT prefix on the
            # task title so the founder can spot the ping at a glance in
            # mobile/desktop notifications. The Slack notifier additionally
            # adds the `[DEMO·HOT]` tag and an optional @-mention (see
            # _demo_prefix / DEMO_HOT_SLACK_MENTION). Public-lane demos
            # keep the original `DEMO:` prefix unchanged.
            if record.is_hot_demo:
                task_title = (
                    f"URGENT! DEMO HOT: verify {len(pending_names)} "
                    f"flagged {schema_name} field(s) for {record.email}"
                )
            else:
                task_title = (
                    f"DEMO: verify {len(pending_names)} {schema_name} field(s) for {record.email}"
                )

            task, _ = await create_task(
                session,
                task=task_title,
                payload=payload,
                payload_schema={"type": "object"},
                response_schema=response_schema,
                timeout_seconds=72 * 3600,
                idempotency_key=f"demo:{demo_id}",
                assign_to=assign_to,
                initial_response={name: ai_values.get(name) for name in pending_names},
                task_metadata={
                    "demo_id": demo_id,
                    "demo_email": record.email,
                    "schema_name": schema_name,
                    "is_hot_demo": str(record.is_hot_demo).lower(),
                },
            )
            record.awaitverify_task_id = task.id
            record.status = DemoStatus.AWAITING_CLAIM
            session.add(record)
            await session.commit()
            logger.info(
                "Created AwaitVerify task %s for demo %s (priority=%s)",
                task.id,
                demo_id,
                priority.value,
            )
        except Exception as exc:
            logger.exception("Failed to create demo AwaitVerify task: %s", exc)
            record.status = DemoStatus.ROUTING_FAILED
            session.add(record)
            await session.commit()
            # TODO(task-15): send_demo_fallback_email(record)
