"""Admin routes for the outbound-webhook delivery queue.

The dispatcher delivers reliably with backoff, but two operator
needs aren't covered by automation:
  - Visibility: "are my webhooks actually being retried, or is
    something stuck?"  → GET /webhook-deliveries
  - Recovery from a multi-day outage: "my receiver was down for 4
    days, can I redrive the abandoned rows?"  → POST /{id}/redeliver

Both are operator-or-admin gated. Read endpoints can grow more
filters later; for now the dominant question is "show me what
went wrong" so we default to status=ABANDONED with a sensible
limit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.core.task_auth import require_operator_or_admin
from awaithumans.server.db.connection import get_session
from awaithumans.server.db.models import WebhookDelivery, WebhookDeliveryStatus
from awaithumans.server.schemas import WebhookDeliveryResponse
from awaithumans.server.services.webhook_dispatch import redeliver

router = APIRouter(prefix="/admin/webhook-deliveries", tags=["admin"])


def _to_response(row: WebhookDelivery) -> WebhookDeliveryResponse:
    return WebhookDeliveryResponse(
        id=row.id,
        task_id=row.task_id,
        url=row.url,
        status=row.status,
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        first_attempted_at=row.first_attempted_at,
        last_attempt_at=row.last_attempt_at,
        last_error=row.last_error,
        last_status_code=row.last_status_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[WebhookDeliveryResponse])
async def list_webhook_deliveries(
    request: Request,
    status: WebhookDeliveryStatus | None = Query(
        default=None,
        description=(
            "Filter to one lifecycle state. Default returns all statuses ordered newest-first."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookDeliveryResponse]:
    """List recent delivery rows, newest first.

    Operators land here when a workflow looks stuck — sorting by
    `updated_at` puts the most recent failures up top so they can
    spot patterns (one URL flapping vs. all tasks for one customer).
    """
    require_operator_or_admin(request)

    stmt = select(WebhookDelivery).order_by(WebhookDelivery.updated_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(WebhookDelivery.status == status)

    rows = (await session.execute(stmt)).scalars().all()
    return [_to_response(r) for r in rows]


@router.post(
    "/{delivery_id}/redeliver",
    response_model=WebhookDeliveryResponse,
)
async def redeliver_webhook(
    delivery_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WebhookDeliveryResponse:
    """Reset a row to PENDING and queue it for the next scheduler tick.

    Works regardless of current status — an already-SUCCEEDED row
    will fire again, an ABANDONED row gets fresh retries with the
    backoff schedule reset to its first delay. Idempotency-key on
    the receiver side is what protects against double-processing.
    """
    require_operator_or_admin(request)

    row = await redeliver(session, delivery_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook delivery not found.")
    return _to_response(row)
