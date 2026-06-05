"""Demo API routes (unauthenticated, gated by Turnstile + rate caps).

The v2 demo surface is two endpoints:

    POST /api/demo/start            (multipart, kicks off extraction)
    GET  /api/demo/{demo_id}/status (polled by the wizard)

No session cookie, no admin bearer. The Turnstile check lives in the
orchestrator (``start_demo``) along with the per-email / per-IP /
global daily / cost-ceiling caps. The dashboard auth middleware lets
``/api/demo/*`` through via the public-prefix allowlist in
``core/auth.py``.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import DemoRecord, Task
from awaithumans.server.schemas.demo import DemoStatusResponse, StartDemoResponse
from awaithumans.server.services.demo.exceptions import DemoRecordNotFoundError
from awaithumans.server.services.demo.service import (
    StartDemoInput,
    _create_awaitverify_task,
    start_demo,
)
from awaithumans.utils.constants import DEMO_MAX_FILE_SIZE_BYTES
from awaithumans.utils.time import to_utc_unix

router = APIRouter(prefix="/demo", tags=["demo"])
logger = logging.getLogger("awaithumans.server.routes.demo")


@router.post("/start", response_model=StartDemoResponse)
async def start_demo_route(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    preset_key: str = Form(...),
    is_hot_demo: bool = Form(default=False),
    turnstile_token: str = Form(..., alias="turnstileToken"),
    page_image: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> StartDemoResponse:
    """Kick off a demo extraction.

    Reads the page PNG, validates size, hashes the caller IP for the
    per-IP cap, and hands off to the orchestrator. The orchestrator
    returns the AI result synchronously; if any fields fell below the
    confidence threshold, it schedules ``_create_awaitverify_task`` to
    run after the response is sent so the route stays fast.
    """
    page_png = await page_image.read()
    if len(page_png) > DEMO_MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large.")

    ip_hash = _hash_ip(request)

    async def _run_background(*, demo_id: str, page_png: bytes) -> None:
        """Wrap the real background runner so the orchestrator stays
        framework-agnostic. FastAPI's ``BackgroundTasks`` accepts a
        callable plus kwargs; we register it here, after start_demo
        has committed the DemoRecord, so the runner picks up the
        record by id once the response is on the wire."""
        background_tasks.add_task(_create_awaitverify_task, demo_id=demo_id, page_png=page_png)

    output = await start_demo(
        session,
        input_=StartDemoInput(
            email=email,
            ip_hash=ip_hash,
            preset_key=preset_key,
            is_hot_demo=is_hot_demo,
            turnstile_token=turnstile_token,
            page_png=page_png,
        ),
        background_runner=_run_background,
    )
    return StartDemoResponse(
        demo_id=output.demo_id,
        ai_result=output.ai_result,
        pending_field_names=output.pending_field_names,
    )


@router.get("/{demo_id}/status", response_model=DemoStatusResponse)
async def demo_status_route(
    demo_id: str,
    session: AsyncSession = Depends(get_session),
) -> DemoStatusResponse:
    """Poll the demo's lifecycle state.

    The wizard hits this every few seconds while waiting for the
    reviewer to claim and submit. ``claimed_by`` reads from the linked
    AwaitVerify task; if the task hasn't been created yet (still in
    ``routing``) the field is null.
    """
    record = await session.get(DemoRecord, demo_id)
    if record is None:
        raise DemoRecordNotFoundError(demo_id)

    claimed_by: str | None = None
    if record.awaitverify_task_id:
        task = await session.get(Task, record.awaitverify_task_id)
        if task is not None:
            claimed_by = task.assigned_to_email

    email_sent_at_unix: int | None = None
    if record.email_sent_at is not None:
        email_sent_at_unix = to_utc_unix(record.email_sent_at)

    return DemoStatusResponse(
        demo_id=record.id,
        status=record.status,
        pending_field_names=record.pending_field_names,
        field_corrections=record.field_corrections,
        awaitverify_task_id=record.awaitverify_task_id,
        claimed_by=claimed_by,
        email_sent_at_unix=email_sent_at_unix,
    )


def _hash_ip(request: Request) -> str:
    """Hash the caller IP for use as the per-IP rate-limit key.

    Prefer ``X-Forwarded-For`` so we see the real visitor IP behind a
    reverse proxy; fall back to the direct client address. The SHA-256
    truncation gives us a stable 32-char opaque key without storing
    raw IPs anywhere (PII minimization).
    """
    raw = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not raw and request.client is not None:
        raw = request.client.host
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
