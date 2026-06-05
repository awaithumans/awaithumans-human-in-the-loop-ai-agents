"""Top-level demo orchestrator.

Sequences Turnstile verify, email gate, rate caps, preset model
resolution, AI extraction, confidence-driven flagging, and the
DemoRecord insert.

The downstream AwaitVerify task creation runs as a background task.
The orchestrator returns to the wizard immediately with the AI result
so it can render the confirmed fields. The reviewer is then routed via
the standard AwaitVerify pipeline using ``priority=demo`` (or
``demo_hot`` for warm prospects).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.config import settings
from awaithumans.server.db.models import DemoRecord, DemoStatus
from awaithumans.server.services.demo.email_gate import validate_demo_email
from awaithumans.server.services.demo.exceptions import DemoSchemaError
from awaithumans.server.services.demo.extractor import (
    ExtractionResult,
    run_demo_extraction,
)
from awaithumans.server.services.demo.presets import build_preset_model
from awaithumans.server.services.demo.rate_limit import (
    DemoCaps,
    check_demo_caps,
    estimate_provider_cost_cents,
)
from awaithumans.server.services.demo.turnstile import verify_turnstile_token

logger = logging.getLogger("awaithumans.server.services.demo.service")


@dataclass
class StartDemoInput:
    email: str
    ip_hash: str
    preset_key: str
    is_hot_demo: bool
    turnstile_token: str
    page_png: bytes


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

    try:
        response_model = build_preset_model(input_.preset_key)
    except KeyError as exc:
        raise DemoSchemaError(f"Unknown preset: {input_.preset_key}") from exc

    extraction: ExtractionResult = await run_demo_extraction(
        page_png=input_.page_png,
        response_model=response_model,
    )

    threshold = settings.DEMO_CONFIDENCE_THRESHOLD
    pending = sorted(name for name, conf in extraction.confidences.items() if conf < threshold)

    record = DemoRecord(
        email=input_.email,
        email_domain=input_.email.rsplit("@", 1)[1].lower(),
        ip_hash=input_.ip_hash,
        is_hot_demo=input_.is_hot_demo,
        preset_key=input_.preset_key,
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
                "preset_key": record.preset_key,
                "ai_values": ai_values,
                "ai_confidences": record.field_confidences,
                "pending_field_names": pending_names,
                "page_image_base64": page_b64,
            }

            task, _ = await create_task(
                session,
                task=(
                    f"DEMO: verify {len(pending_names)} flagged "
                    f"{record.preset_key} field(s) for {record.email}"
                ),
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
                    "preset_key": record.preset_key,
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
