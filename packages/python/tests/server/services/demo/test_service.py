from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from awaithumans.server.db.models import DemoRecord, DemoStatus
from awaithumans.server.services.demo.exceptions import (
    DemoPerEmailCapError,
    DemoSchemaError,
    InvalidDemoEmailError,
    TurnstileVerificationError,
)
from awaithumans.server.services.demo.extractor import ExtractionResult
from awaithumans.server.services.demo.service import StartDemoInput, start_demo


def _input(**overrides) -> StartDemoInput:
    base = {
        "email": "alice@acme.com",
        "ip_hash": "ip-1",
        "preset_key": "Receipt",
        "is_hot_demo": False,
        "turnstile_token": "t",
        "page_png": b"\x89PNG fake",
    }
    base.update(overrides)
    return StartDemoInput(**base)


@pytest.mark.asyncio
async def test_happy_path_creates_demo_record(db_session: AsyncSession) -> None:
    with (
        patch("awaithumans.server.services.demo.service.verify_turnstile_token") as mock_t,
        patch("awaithumans.server.services.demo.service.run_demo_extraction") as mock_run,
    ):
        mock_t.return_value = None
        mock_run.return_value = ExtractionResult(
            values={"vendor": "Acme", "total_cents": 1299, "date": "2026-01-01"},
            confidences={"vendor": 0.97, "total_cents": 0.62, "date": 0.95},
            cost_cents=6,
        )

        output = await start_demo(db_session, input_=_input())

    assert output.demo_id
    assert output.ai_result == {
        "vendor": "Acme",
        "total_cents": 1299,
        "date": "2026-01-01",
    }
    assert output.pending_field_names == ["total_cents"]  # < 0.85 threshold

    from sqlalchemy import select

    rows = (await db_session.execute(select(DemoRecord))).scalars().all()
    assert len(rows) == 1
    record = rows[0]
    assert record.preset_key == "Receipt"
    assert record.is_hot_demo is False
    assert record.field_confidences == {
        "vendor": 0.97,
        "total_cents": 0.62,
        "date": 0.95,
    }
    assert record.pending_field_names == ["total_cents"]
    assert record.status == DemoStatus.ROUTING


@pytest.mark.asyncio
async def test_hot_demo_flag_persisted(db_session: AsyncSession) -> None:
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        patch("awaithumans.server.services.demo.service.run_demo_extraction") as mock_run,
    ):
        mock_run.return_value = ExtractionResult(
            values={"vendor": "Acme", "total_cents": 1299, "date": "2026-01-01"},
            confidences={"vendor": 0.97, "total_cents": 0.92, "date": 0.95},
            cost_cents=6,
        )
        await start_demo(db_session, input_=_input(is_hot_demo=True))

    from sqlalchemy import select

    record = (await db_session.execute(select(DemoRecord))).scalars().one()
    assert record.is_hot_demo is True
    assert record.pending_field_names == []  # all above threshold


@pytest.mark.asyncio
async def test_rejects_bad_email(db_session: AsyncSession) -> None:
    with pytest.raises(InvalidDemoEmailError):
        await start_demo(db_session, input_=_input(email="alice@gmail.com"))


@pytest.mark.asyncio
async def test_rejects_bad_turnstile(db_session: AsyncSession) -> None:
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            side_effect=TurnstileVerificationError(),
        ),
        pytest.raises(TurnstileVerificationError),
    ):
        await start_demo(db_session, input_=_input())


@pytest.mark.asyncio
async def test_rejects_unknown_preset(db_session: AsyncSession) -> None:
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        pytest.raises(DemoSchemaError),
    ):
        await start_demo(db_session, input_=_input(preset_key="NotAPreset"))


@pytest.mark.asyncio
async def test_caps_block_second_call(db_session: AsyncSession) -> None:
    db_session.add(
        DemoRecord(
            email="alice@acme.com",
            email_domain="acme.com",
            ip_hash="ip-1",
            preset_key="Receipt",
            status=DemoStatus.EMAIL_SENT,
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        pytest.raises(DemoPerEmailCapError),
    ):
        await start_demo(db_session, input_=_input())
