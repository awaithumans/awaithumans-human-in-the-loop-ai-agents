"""Background scheduler: send the AI-only fallback email for demo records
stuck in the review pipeline longer than DEMO_FALLBACK_AFTER_HOURS.

Modeled on ``timeout_scheduler.py``: polls every 5 minutes, filters by
status to be idempotent, and acquires a short-lived DB session per tick
so we never hold a connection open between checks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from awaithumans.server.channels.email.demo_email import send_demo_fallback_email
from awaithumans.server.core.config import settings
from awaithumans.server.db.connection import get_async_session_factory
from awaithumans.server.db.models import DemoRecord, DemoStatus

logger = logging.getLogger("awaithumans.server.services.demo.fallback_scheduler")

_POLL_INTERVAL_SECONDS = 300  # 5 minutes


# Statuses where the reviewer never completed the task. Once the record
# reaches REVIEW_COMPLETE / EMAIL_SENT / FALLBACK_SENT / EMAIL_FAILED /
# ROUTING_FAILED the receipt or immediate-fallback paths have already
# taken over, so excluding those keeps this scheduler idempotent.
_STUCK_STATUSES = (
    DemoStatus.ROUTING,
    DemoStatus.AWAITING_CLAIM,
    DemoStatus.CLAIMED,
    DemoStatus.PARTIALLY_DONE,
)


async def run_fallback_scheduler() -> None:
    """Run the demo fallback scheduler loop.

    Every ``_POLL_INTERVAL_SECONDS`` seconds, find demo records that have
    been stuck in a pre-completion status for longer than
    ``settings.DEMO_FALLBACK_AFTER_HOURS`` and send the AI-only fallback
    email. ``send_demo_fallback_email`` updates the record to
    ``FALLBACK_SENT`` on success, so subsequent ticks skip it.
    """
    logger.info(
        "Demo fallback scheduler started (check interval: %ds, threshold: %dh)",
        _POLL_INTERVAL_SECONDS,
        settings.DEMO_FALLBACK_AFTER_HOURS,
    )

    while True:
        try:
            await _process_pending()
        except Exception:
            logger.exception("Demo fallback scheduler tick failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _process_pending() -> None:
    """Find stuck demo records past the threshold and send fallback emails."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.DEMO_FALLBACK_AFTER_HOURS)
    factory = get_async_session_factory()
    async with factory() as session:
        query = select(DemoRecord).where(
            DemoRecord.status.in_(_STUCK_STATUSES),
            DemoRecord.created_at < cutoff,
        )
        rows = (await session.execute(query)).scalars().all()

    for record in rows:
        logger.info(
            "Demo %s exceeded %dh; sending fallback (status=%s)",
            record.id,
            settings.DEMO_FALLBACK_AFTER_HOURS,
            record.status.value,
        )
        await send_demo_fallback_email(record)
