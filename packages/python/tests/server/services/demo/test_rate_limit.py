from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import DemoRecord, DemoStatus
from awaithumans.server.services.demo.exceptions import (
    DemoCapacityError,
    DemoPerEmailCapError,
)
from awaithumans.server.services.demo.rate_limit import (
    DemoCaps,
    check_demo_caps,
    estimate_provider_cost_cents,
)


@pytest.fixture
def caps() -> DemoCaps:
    return DemoCaps(
        per_email_window_days=7,
        per_ip_window_hours=24,
        per_ip_cap=3,
        daily_cap=50,
        daily_cost_ceiling_cents=500,
    )


def _seed(
    email: str = "alice@acme.com",
    ip_hash: str = "ip-1",
    age: timedelta = timedelta(hours=1),
    cost_cents: int = 5,
    status: DemoStatus = DemoStatus.EMAIL_SENT,
) -> DemoRecord:
    return DemoRecord(
        email=email,
        email_domain=email.split("@", 1)[1],
        ip_hash=ip_hash,
        schema_spec={
            "name": "Invoice",
            "fields": [{"name": "vendor", "type": "str"}],
        },
        cost_estimate_cents=cost_cents,
        status=status,
        created_at=datetime.now(timezone.utc) - age,
    )


@pytest.mark.asyncio
async def test_passes_when_empty(db_session: AsyncSession, caps: DemoCaps) -> None:
    await check_demo_caps(
        db_session,
        email="alice@acme.com",
        ip_hash="ip-1",
        per_call_cost_cents=5,
        caps=caps,
    )


@pytest.mark.asyncio
async def test_per_email_weekly_cap(db_session: AsyncSession, caps: DemoCaps) -> None:
    db_session.add(_seed(age=timedelta(days=2)))
    await db_session.commit()
    with pytest.raises(DemoPerEmailCapError):
        await check_demo_caps(
            db_session,
            email="alice@acme.com",
            ip_hash="ip-2",
            per_call_cost_cents=5,
            caps=caps,
        )


@pytest.mark.asyncio
async def test_per_email_cap_expires(db_session: AsyncSession, caps: DemoCaps) -> None:
    db_session.add(_seed(age=timedelta(days=8)))
    await db_session.commit()
    await check_demo_caps(
        db_session,
        email="alice@acme.com",
        ip_hash="ip-2",
        per_call_cost_cents=5,
        caps=caps,
    )


@pytest.mark.asyncio
async def test_per_ip_daily_cap(db_session: AsyncSession, caps: DemoCaps) -> None:
    for n in range(3):
        db_session.add(_seed(email=f"u{n}@acme.com", ip_hash="ip-X"))
    await db_session.commit()
    with pytest.raises(DemoCapacityError):
        await check_demo_caps(
            db_session,
            email="new@acme.com",
            ip_hash="ip-X",
            per_call_cost_cents=5,
            caps=caps,
        )


@pytest.mark.asyncio
async def test_global_daily_cap(db_session: AsyncSession, caps: DemoCaps) -> None:
    for n in range(50):
        db_session.add(_seed(email=f"u{n}@acme.com", ip_hash=f"ip-{n}"))
    await db_session.commit()
    with pytest.raises(DemoCapacityError):
        await check_demo_caps(
            db_session,
            email="new@acme.com",
            ip_hash="ip-new",
            per_call_cost_cents=5,
            caps=caps,
        )


@pytest.mark.asyncio
async def test_cost_ceiling(db_session: AsyncSession, caps: DemoCaps) -> None:
    db_session.add(_seed(cost_cents=498))
    await db_session.commit()
    with pytest.raises(DemoCapacityError):
        await check_demo_caps(
            db_session,
            email="new@acme.com",
            ip_hash="ip-Y",
            per_call_cost_cents=5,
            caps=caps,
        )


def test_estimate_provider_cost_returns_cents() -> None:
    # v2 has a single provider; cost is always non-zero.
    assert estimate_provider_cost_cents() > 0
