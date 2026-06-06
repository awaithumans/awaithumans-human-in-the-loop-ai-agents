"""Demo API routes (unauthenticated, gated by Turnstile + rate caps).

The v2 demo surface is four endpoints:

    POST /api/demo/propose-schema   (multipart, LLM proposes a schema)
    POST /api/demo/start            (multipart, kicks off extraction)
    GET  /api/demo/{demo_id}/status (polled by the wizard)
    POST /api/demo/{demo_id}/field/{field_name}/submit (reviewer correction)

No session cookie, no admin bearer on the public routes. The Turnstile
check lives in the orchestrator (``start_demo``) along with the
per-email / per-IP / global daily / cost-ceiling caps. The dashboard
auth middleware lets ``/api/demo/*`` through via the public-prefix
allowlist in ``core/auth.py``.

The ``propose-schema`` endpoint is a lightweight pre-flight that
doesn't write a DemoRecord; abuse is bounded by a simple in-memory
per-IP token bucket (best-effort, single-process). The full rate-limit
machinery in ``services/demo/rate_limit.py`` only runs on ``/start``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.auth import (
    InvalidSessionError,
    SessionClaims,
    verify_session,
)
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import DemoRecord, Task
from awaithumans.server.schemas.demo import DemoStatusResponse, StartDemoResponse
from awaithumans.server.services.demo.exceptions import (
    DemoCapacityError,
    DemoRecordNotFoundError,
    DemoSchemaError,
)
from awaithumans.server.services.demo.schema_proposer import (
    propose_schema_from_page,
)
from awaithumans.server.services.demo.service import (
    StartDemoInput,
    _create_awaitverify_task,
    start_demo,
    submit_demo_field,
)
from awaithumans.server.services.user_service import get_user
from awaithumans.utils.constants import (
    DASHBOARD_SESSION_COOKIE_NAME,
    DEMO_MAX_FILE_SIZE_BYTES,
)
from awaithumans.utils.time import to_utc_unix

router = APIRouter(prefix="/demo", tags=["demo"])
logger = logging.getLogger("awaithumans.server.routes.demo")


# ─── Propose schema (pre-flight, LLM-driven) ───────────────────────────

# Per-IP token bucket for /propose-schema. Single-process, in-memory,
# best-effort. The full rate-limit machinery (services/demo/rate_limit.py)
# only runs on /start; this lightweight guard keeps the pre-flight call
# from being used as a cheap LLM relay. Sized for a demo, not a CDN.
_PROPOSE_PER_IP_CAP = 30
_PROPOSE_WINDOW_SECONDS = 3600
_PROPOSE_IP_BUCKETS: dict[str, list[float]] = {}


def _check_propose_ip_bucket(ip_hash: str) -> None:
    """Tick the per-IP propose bucket. Raise DemoCapacityError if full.

    Drops timestamps older than the window before counting so the
    bucket self-trims without a separate sweep. The dict can grow
    indefinitely across many distinct IPs; for the v2 demo footprint
    that's bounded by the global daily cap on /start anyway.
    """
    now = time.monotonic()
    cutoff = now - _PROPOSE_WINDOW_SECONDS
    bucket = _PROPOSE_IP_BUCKETS.get(ip_hash, [])
    bucket = [t for t in bucket if t >= cutoff]
    if len(bucket) >= _PROPOSE_PER_IP_CAP:
        _PROPOSE_IP_BUCKETS[ip_hash] = bucket
        raise DemoCapacityError()
    bucket.append(now)
    _PROPOSE_IP_BUCKETS[ip_hash] = bucket


@router.post("/propose-schema")
async def propose_schema_route(
    request: Request,
    page_image: UploadFile = File(...),
) -> dict[str, Any]:
    """Ask the LLM to propose a Pydantic schema for the page.

    This is a pre-flight aid: the wizard renders the proposed schema in
    an editable form so the visitor can tweak field names / types
    before paying for a full extraction. No DemoRecord is written
    here; the only anti-abuse guard is a simple per-IP token bucket.
    The visible page is the only input; no email, no Turnstile.
    """
    page_png = await page_image.read()
    if len(page_png) > DEMO_MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large.")

    ip_hash = _hash_ip(request)
    _check_propose_ip_bucket(ip_hash)

    spec = await propose_schema_from_page(page_png)
    return spec.model_dump()


# ─── Start demo (extraction kickoff) ───────────────────────────────────


@router.post("/start", response_model=StartDemoResponse)
async def start_demo_route(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    schema_spec_json: str = Form(...),
    is_hot_demo: bool = Form(default=False),
    turnstile_token: str = Form(..., alias="turnstileToken"),
    page_image: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> StartDemoResponse:
    """Kick off a demo extraction.

    Reads the page PNG, validates size, hashes the caller IP for the
    per-IP cap, parses the visitor-supplied schema spec, and hands off
    to the orchestrator. The orchestrator returns the AI result
    synchronously; if any fields fell below the confidence threshold,
    it schedules ``_create_awaitverify_task`` to run after the response
    is sent so the route stays fast.
    """
    page_png = await page_image.read()
    if len(page_png) > DEMO_MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large.")

    try:
        schema_spec = json.loads(schema_spec_json)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DemoSchemaError(f"Invalid schema spec JSON: {exc}") from exc

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
            schema_spec=schema_spec,
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


# ─── Per-field submit ──────────────────────────────────────────────────


class SubmitFieldBody(BaseModel):
    """Request body for the per-field submit endpoint.

    ``value`` is intentionally ``Any``: the visitor's schema defines
    the per-field types, and Pydantic's JSON parser accepts any JSON
    primitive (string, number, bool, null, array, object). Stricter
    per-schema validation lives in the dynamic model and can be wired
    in here in a follow-up if reviewer typos start landing as garbage."""

    value: Any


@router.post("/{demo_id}/field/{field_name}/submit")
async def submit_demo_field_route(
    demo_id: str,
    field_name: str,
    body: SubmitFieldBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Apply one reviewer correction to a demo record.

    The reviewer's browser hits this once per field; each call returns
    the new ``status`` + remaining ``pending_field_names`` so the
    dashboard can render its progress UI. When the last pending field
    lands, the linked AwaitVerify task is completed and the receipt
    email fires out-of-band.

    Auth: the route is under the public ``/api/demo/`` prefix (so the
    visitor's status polling endpoint stays cookie-less), so this
    handler does its own session check rather than relying on the
    dashboard auth middleware. Same allow-list as
    ``/api/tasks/{id}/complete``: operators and admin-bearer callers.
    """
    reviewer_email = await _require_demo_reviewer(request, session)
    result = await submit_demo_field(
        session,
        demo_id=demo_id,
        field_name=field_name,
        value=body.value,
        reviewer_email=reviewer_email,
    )
    return {
        "demo_id": result.demo_id,
        "status": result.status.value,
        "pending_field_names": result.pending_field_names,
        "field_corrections": result.field_corrections,
    }


async def _require_demo_reviewer(
    request: Request,
    session: AsyncSession,
) -> str | None:
    """Resolve the reviewer's email from the session cookie or 401/403.

    Mirrors the dashboard auth middleware for the per-field submit path
    (which sits under the public ``/api/demo/`` prefix, so the
    middleware doesn't run its cookie check). Admin-bearer callers
    return ``None`` so the service skips the per-task assignee check,
    matching the regular task complete route where the agent's
    bearer token isn't a human identity.
    """
    # Admin-bearer escape hatch (parity with the rest of the API).
    if getattr(request.state, "auth_admin_token", False):
        return None

    cookie = request.cookies.get(DASHBOARD_SESSION_COOKIE_NAME)
    if not cookie:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )

    try:
        claims: SessionClaims = verify_session(cookie)
    except InvalidSessionError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session.",
        ) from None

    if not claims.is_operator:
        raise HTTPException(
            status_code=403,
            detail="Operator role required.",
        )

    user = await get_user(session, claims.user_id)
    if user is None or not user.active:
        raise HTTPException(
            status_code=401,
            detail="Session user no longer active.",
        )
    if not user.is_operator:
        # Defensive: the claim says operator but the DB row was demoted.
        raise HTTPException(
            status_code=403,
            detail="Operator role required.",
        )

    return user.email
