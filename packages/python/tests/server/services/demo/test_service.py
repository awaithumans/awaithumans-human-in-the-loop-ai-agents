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
from awaithumans.server.services.demo.service import (
    StartDemoInput,
    start_demo,
    submit_demo_field,
)

_RECEIPT_SPEC = {
    "name": "Receipt",
    "fields": [
        {"name": "vendor", "type": "str"},
        {"name": "total_cents", "type": "int"},
        {"name": "date", "type": "str"},
    ],
}


def _input(**overrides) -> StartDemoInput:
    base = {
        "email": "alice@acme.com",
        "ip_hash": "ip-1",
        "schema_spec": _RECEIPT_SPEC,
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
    assert record.schema_spec["name"] == "Receipt"
    assert record.schema_name == "Receipt"
    assert record.is_hot_demo is False
    assert record.field_confidences == {
        "vendor": 0.97,
        "total_cents": 0.62,
        "date": 0.95,
    }
    assert record.pending_field_names == ["total_cents"]
    assert record.status == DemoStatus.ROUTING


@pytest.mark.asyncio
async def test_auto_proposes_when_schema_omitted(db_session: AsyncSession) -> None:
    """When the caller omits a schema spec, the orchestrator asks the
    LLM to propose one before running extraction. The proposed spec
    persists on the DemoRecord and the proposer is called exactly once.

    The collapsed 3-step wizard relies on this path: it no longer asks
    the visitor to review or edit a schema, so every call from the
    landing page hits this branch.
    """
    from awaithumans.server.services.demo.schema_builder import (
        SchemaFieldSpec,
        SchemaSpec,
    )

    proposed = SchemaSpec(
        name="Receipt",
        fields=[
            SchemaFieldSpec(name="vendor", type="str"),
            SchemaFieldSpec(name="total_cents", type="int"),
            SchemaFieldSpec(name="date", type="str"),
        ],
    )

    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        patch(
            "awaithumans.server.services.demo.service.propose_schema_from_page",
            return_value=proposed,
        ) as mock_propose,
        patch("awaithumans.server.services.demo.service.run_demo_extraction") as mock_run,
    ):
        mock_run.return_value = ExtractionResult(
            values={"vendor": "Acme", "total_cents": 1299, "date": "2026-01-01"},
            confidences={"vendor": 0.97, "total_cents": 0.62, "date": 0.95},
            cost_cents=6,
        )
        # Note: schema_spec is intentionally omitted so the dataclass
        # default (None) kicks in and the orchestrator hits the
        # auto-propose path.
        output = await start_demo(
            db_session,
            input_=StartDemoInput(
                email="alice@acme.com",
                ip_hash="ip-1",
                turnstile_token="t",
                page_png=b"\x89PNG fake",
            ),
        )

    assert mock_propose.call_count == 1
    assert output.demo_id

    from sqlalchemy import select

    record = (await db_session.execute(select(DemoRecord))).scalars().one()
    assert record.schema_spec is not None
    assert record.schema_spec["name"] == "Receipt"
    assert [f["name"] for f in record.schema_spec["fields"]] == [
        "vendor",
        "total_cents",
        "date",
    ]


@pytest.mark.asyncio
async def test_hot_demo_flag_persisted(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Disable the demo floor so this test exercises the
    # threshold-only path. With the floor enabled (default 30%) the
    # is_hot_demo flag is what we're asserting on, not the flagged
    # count, so the simpler assertion is to confirm the hot flag
    # persists when all confidences are clean.
    from awaithumans.server.core.config import settings as _settings

    monkeypatch.setattr(_settings, "DEMO_MIN_FLAGGED_RATIO", 0.0)
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        patch("awaithumans.server.services.demo.service.run_demo_extraction") as mock_run,
    ):
        mock_run.return_value = ExtractionResult(
            values={"vendor": "Acme", "total_cents": 1299, "date": "2026-01-01"},
            confidences={"vendor": 0.99, "total_cents": 0.97, "date": 0.98},
            cost_cents=6,
        )
        await start_demo(db_session, input_=_input(is_hot_demo=True))

    from sqlalchemy import select

    record = (await db_session.execute(select(DemoRecord))).scalars().one()
    assert record.is_hot_demo is True
    assert record.pending_field_names == []  # all above threshold


@pytest.mark.asyncio
async def test_demo_floor_forces_min_flagged_ratio(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when the LLM is uniformly confident, the demo floor forces
    the lowest-confidence fields into pending so the visitor always
    sees the human-review moment.

    Floor is configurable; this test sets it to 50% with a 4-field
    schema and asserts that the two lowest-confidence fields land in
    pending despite every value being above the threshold.
    """
    from awaithumans.server.core.config import settings as _settings

    monkeypatch.setattr(_settings, "DEMO_MIN_FLAGGED_RATIO", 0.5)

    four_field_spec = {
        "name": "Receipt",
        "fields": [
            {"name": "vendor", "type": "str"},
            {"name": "total_cents", "type": "int"},
            {"name": "date", "type": "str"},
            {"name": "tax_cents", "type": "int"},
        ],
    }
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        patch("awaithumans.server.services.demo.service.run_demo_extraction") as mock_run,
    ):
        mock_run.return_value = ExtractionResult(
            values={
                "vendor": "Acme",
                "total_cents": 1299,
                "date": "2026-01-01",
                "tax_cents": 100,
            },
            confidences={
                "vendor": 0.99,
                "total_cents": 0.98,
                "date": 0.99,
                "tax_cents": 0.97,
            },
            cost_cents=6,
        )
        output = await start_demo(
            db_session,
            input_=_input(schema_spec=four_field_spec),
        )

    # 50% of 4 = 2 fields must be pending. The two lowest-confidence
    # fields (tax_cents at 0.97, then total_cents at 0.98) get
    # promoted into the pending set.
    assert len(output.pending_field_names) == 2
    assert set(output.pending_field_names) == {"tax_cents", "total_cents"}


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
async def test_rejects_malformed_schema_spec(db_session: AsyncSession) -> None:
    """A malformed schema spec (non-identifier field name, unsupported
    type, missing name, etc.) surfaces as DemoSchemaError so the route
    returns 400 with the validator's message."""
    with (
        patch(
            "awaithumans.server.services.demo.service.verify_turnstile_token",
            return_value=None,
        ),
        pytest.raises(DemoSchemaError),
    ):
        await start_demo(
            db_session,
            input_=_input(
                schema_spec={
                    "name": "NotAClass!",  # not a valid Python identifier
                    "fields": [{"name": "vendor", "type": "str"}],
                }
            ),
        )


@pytest.mark.asyncio
async def test_caps_block_second_call(db_session: AsyncSession) -> None:
    db_session.add(
        DemoRecord(
            email="alice@acme.com",
            email_domain="acme.com",
            ip_hash="ip-1",
            schema_spec=_RECEIPT_SPEC,
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


# ─── Schema proposer (service-level) ───────────────────────────────────


@pytest.mark.asyncio
async def test_propose_schema_returns_spec() -> None:
    """The proposer parses the LLM JSON into a SchemaSpec. Azure call
    is mocked at the chat.completions boundary so the test stays
    hermetic."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from awaithumans.server.services.demo.schema_proposer import (
        propose_schema_from_page,
    )

    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"name": "Invoice", "fields": ['
                        '{"name": "vendor", "type": "str"},'
                        '{"name": "total_cents", "type": "int"},'
                        '{"name": "invoice_date", "type": "date"}'
                        "]}"
                    )
                )
            )
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=fake_response),
            )
        )
    )
    with patch(
        "awaithumans.server.services.demo.schema_proposer._build_client",
        return_value=fake_client,
    ):
        spec = await propose_schema_from_page(b"\x89PNG fake")

    assert spec.name == "Invoice"
    assert [f.name for f in spec.fields] == ["vendor", "total_cents", "invoice_date"]
    assert [f.type for f in spec.fields] == ["str", "int", "date"]


# ─── Per-field submit ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_field_updates_correction(db_session: AsyncSession) -> None:
    """First-of-many submission: status flips to partially_done and the
    field lands in field_corrections."""
    record = DemoRecord(
        email="alice@acme.com",
        email_domain="acme.com",
        ip_hash="ip-1",
        schema_spec=_RECEIPT_SPEC,
        ai_result={"vendor": "Acme", "total_cents": 1299, "date": "2026-01-01"},
        field_confidences={"vendor": 0.97, "total_cents": 0.62, "date": 0.7},
        pending_field_names=["total_cents", "date"],
        status=DemoStatus.AWAITING_CLAIM,
        awaitverify_task_id=None,  # link not required for this test
    )
    db_session.add(record)
    await db_session.commit()

    result = await submit_demo_field(
        db_session,
        demo_id=record.id,
        field_name="total_cents",
        value=1234,
        reviewer_email=None,  # skip authz check by passing None
    )

    assert result.pending_field_names == ["date"]
    assert result.field_corrections == {"total_cents": 1234}
    assert result.status == DemoStatus.PARTIALLY_DONE


@pytest.mark.asyncio
async def test_submit_unknown_field_raises(db_session: AsyncSession) -> None:
    """Anti-typo / anti-replay: a field name not in pending raises
    DemoSchemaError, which the route maps to 400."""
    record = DemoRecord(
        email="alice@acme.com",
        email_domain="acme.com",
        ip_hash="ip-1",
        schema_spec=_RECEIPT_SPEC,
        ai_result={"vendor": "Acme"},
        field_confidences={"vendor": 0.97},
        pending_field_names=["vendor"],
        status=DemoStatus.AWAITING_CLAIM,
    )
    db_session.add(record)
    await db_session.commit()

    with pytest.raises(DemoSchemaError):
        await submit_demo_field(
            db_session,
            demo_id=record.id,
            field_name="not_a_field",
            value="x",
            reviewer_email=None,
        )


@pytest.mark.asyncio
async def test_submit_completes_when_last_field(db_session: AsyncSession) -> None:
    """Final field submission: status flips to review_complete, the
    receipt email is fired out-of-band."""
    record = DemoRecord(
        email="alice@acme.com",
        email_domain="acme.com",
        ip_hash="ip-1",
        schema_spec=_RECEIPT_SPEC,
        ai_result={"vendor": "Acme", "total_cents": 1299},
        field_confidences={"vendor": 0.97, "total_cents": 0.62},
        pending_field_names=["total_cents"],
        status=DemoStatus.CLAIMED,
        field_corrections={},
    )
    db_session.add(record)
    await db_session.commit()

    # The email send is scheduled via asyncio.create_task; patching the
    # send fn itself is enough to assert it was invoked. We await the
    # spawned task at the end so the test event loop doesn't drop it
    # as "unawaited" noise.
    async def _noop(record):  # type: ignore[no-untyped-def]
        return None

    with patch(
        "awaithumans.server.services.demo.service.send_demo_review_complete_email",
        side_effect=_noop,
    ) as mock_send:
        result = await submit_demo_field(
            db_session,
            demo_id=record.id,
            field_name="total_cents",
            value=1234,
            reviewer_email=None,
        )
        # Give the create_task'd coroutine a tick to run so the mock
        # records the call before the test asserts on it.
        import asyncio

        await asyncio.sleep(0)

    assert result.pending_field_names == []
    assert result.status == DemoStatus.REVIEW_COMPLETE
    mock_send.assert_called_once()
