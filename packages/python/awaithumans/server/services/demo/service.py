"""Top-level demo orchestrator.

Sequences Turnstile verify, email gate, rate caps, preset model
resolution, AI extraction, confidence-driven flagging, and the
DemoRecord insert.

The downstream AwaitVerify-task-creation is wired in Task 13 (it
needs the route layer plus fragmentation). Here we accept an optional
background_runner so the orchestrator stays testable in isolation
and Task 13 can attach the real background dispatcher.
"""

from __future__ import annotations

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
        await background_runner(demo_id=record.id)

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
