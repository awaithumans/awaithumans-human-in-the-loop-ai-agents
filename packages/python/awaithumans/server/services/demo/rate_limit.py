"""Per-email, per-IP, global, and cost ceiling caps.

Same copy ("Demo's at capacity") returned for per-IP / global / cost
hits so the response doesn't leak which gate tripped. Per-email error
is distinct because the visitor knows they used the demo and we can
nudge them straight to signup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import DemoRecord, DemoStatus
from awaithumans.server.services.demo.exceptions import (
    DemoCapacityError,
    DemoPerEmailCapError,
)

# Flat per-call estimate for the v2 single-provider design. Used both
# as the pre-call gate input and as the default DemoRecord cost stamp.
_PROVIDER_COST_CENTS = 6


@dataclass(frozen=True)
class DemoCaps:
    per_email_window_days: int
    per_ip_window_hours: int
    per_ip_cap: int
    daily_cap: int
    daily_cost_ceiling_cents: int


# Every DemoStatus value counts toward the rate limits: each represents
# work that consumed budget (provider call, DB row, reviewer time, or
# email send). Terminal-but-failed statuses are still budget-spent.
_COUNTED_STATUSES = tuple(DemoStatus)


def estimate_provider_cost_cents() -> int:
    return _PROVIDER_COST_CENTS


async def check_demo_caps(
    session: AsyncSession,
    *,
    email: str,
    ip_hash: str,
    per_call_cost_cents: int,
    caps: DemoCaps,
) -> None:
    now = datetime.now(timezone.utc)
    midnight_utc = now.replace(hour=0, minute=0, second=0, microsecond=0)

    email_since = now - timedelta(days=caps.per_email_window_days)
    email_count_q = (
        select(func.count())
        .select_from(DemoRecord)
        .where(
            DemoRecord.email == email,
            DemoRecord.created_at >= email_since,
            DemoRecord.status.in_(_COUNTED_STATUSES),
        )
    )
    email_count = (await session.execute(email_count_q)).scalar_one()
    if email_count >= 1:
        raise DemoPerEmailCapError()

    ip_since = now - timedelta(hours=caps.per_ip_window_hours)
    ip_count_q = (
        select(func.count())
        .select_from(DemoRecord)
        .where(
            DemoRecord.ip_hash == ip_hash,
            DemoRecord.created_at >= ip_since,
            DemoRecord.status.in_(_COUNTED_STATUSES),
        )
    )
    ip_count = (await session.execute(ip_count_q)).scalar_one()
    if ip_count >= caps.per_ip_cap:
        raise DemoCapacityError()

    day_count_q = (
        select(func.count())
        .select_from(DemoRecord)
        .where(
            DemoRecord.created_at >= midnight_utc,
            DemoRecord.status.in_(_COUNTED_STATUSES),
        )
    )
    day_count = (await session.execute(day_count_q)).scalar_one()
    if day_count >= caps.daily_cap:
        raise DemoCapacityError()

    day_cost_q = select(func.coalesce(func.sum(DemoRecord.cost_estimate_cents), 0)).where(
        DemoRecord.created_at >= midnight_utc,
        DemoRecord.status.in_(_COUNTED_STATUSES),
    )
    day_cost = (await session.execute(day_cost_q)).scalar_one()
    if day_cost + per_call_cost_cents > caps.daily_cost_ceiling_cents:
        raise DemoCapacityError()
