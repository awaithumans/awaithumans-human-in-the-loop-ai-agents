"""Tests for extract_document / extract_envelope — machine extraction.

The managed HTTP call is stubbed at the _post_json seam (same
pattern as the await_review tests): these cover argument
validation, request assembly, typed response parsing, and the
timeout selection that makes blocking human review safe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from awaithumans.awaitverify import machine as machine_mod
from awaithumans.awaitverify.errors import VerifyDocumentArgError
from awaithumans.awaitverify.machine import (
    ExtractionResult,
    extract_document,
    extract_document_sync,
    extract_envelope,
)

_WIRE_RESPONSE: dict[str, Any] = {
    "data": {"surname": "De Bruijn", "passport_number": "SPECI2014"},
    "fields": {
        "surname": {
            "path": "surname",
            "confidence": 0.9,
            "agreement": 1.0,
            "flags": ["PROVISIONAL_CALIBRATION"],
        },
        "passport_number": {
            "path": "passport_number",
            "confidence": 0.99,
            "agreement": 1.0,
            "flags": ["PROVISIONAL_CALIBRATION"],
        },
    },
    "document_confidence": 0.945,
    "doc_type": "passport",
    "calibration": {"version": "provisional-2026-07", "calibrated": False},
    "pages": 1,
    "usage": {"pages": 1, "samples": 3, "cost_cents": 25, "balance_after_cents": 975},
}


class _Recorder:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def page(tmp_path: Path) -> Path:
    path = tmp_path / "passport.png"
    path.write_bytes(b"\x89PNG fake bytes")
    return path


async def test_happy_path_typed_result(
    page: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_WIRE_RESPONSE)
    monkeypatch.setattr(machine_mod, "_post_json", recorder)
    result = await extract_document(
        document_path=page, doc_type="passport", human_review="off", api_key="ah_sk_test"
    )
    assert isinstance(result, ExtractionResult)
    assert result.data["passport_number"] == "SPECI2014"
    assert result.fields["surname"].confidence == 0.9
    assert result.calibration.calibrated is False
    assert result.usage.cost_cents == 25

    call = recorder.calls[0]
    assert call["url"].endswith("/api/v1/extract")
    assert call["api_key"] == "ah_sk_test"
    assert call["body"]["media_type"] == "image/png"
    assert call["body"]["doc_type"] == "passport"
    assert call["body"]["human_review"] == "off"
    # Machine-only calls use the fast timeout.
    assert call["http_timeout_seconds"] == machine_mod._MACHINE_TIMEOUT_SECONDS


async def test_human_review_default_is_low_confidence(
    page: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_WIRE_RESPONSE)
    monkeypatch.setattr(machine_mod, "_post_json", recorder)
    await extract_document(document_path=page, doc_type="passport", api_key="k")
    call = recorder.calls[0]
    assert call["body"]["human_review"] == "low_confidence"
    # The POST always returns fast (reviews resolve via polling), so
    # the HTTP timeout stays at the machine default.
    assert call["http_timeout_seconds"] == machine_mod._MACHINE_TIMEOUT_SECONDS


async def test_custom_schema_body(page: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_WIRE_RESPONSE)
    monkeypatch.setattr(machine_mod, "_post_json", recorder)
    schema = {"name": "Invoice", "fields": [{"name": "vendor", "type": "string"}]}
    await extract_document(
        document_path=page, response_schema=schema, human_review="off", api_key="k"
    )
    body = recorder.calls[0]["body"]
    assert body["response_schema"] == schema
    assert "doc_type" not in body


async def test_exactly_one_schema_choice(page: Path) -> None:
    with pytest.raises(VerifyDocumentArgError):
        await extract_document(document_path=page, api_key="k")
    with pytest.raises(VerifyDocumentArgError):
        await extract_document(
            document_path=page,
            doc_type="passport",
            response_schema={"fields": []},
            api_key="k",
        )


async def test_exactly_one_document_source(page: Path) -> None:
    with pytest.raises(VerifyDocumentArgError):
        await extract_document(doc_type="passport", api_key="k")
    with pytest.raises(VerifyDocumentArgError):
        await extract_document(
            document_path=page,
            document_url="https://example.com/x.png",
            doc_type="passport",
            api_key="k",
        )


async def test_unsupported_suffix_needs_media_type(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF fake")
    with pytest.raises(VerifyDocumentArgError):
        await extract_document(document_path=pdf, doc_type="passport", api_key="k")


def test_sync_wrapper(page: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_WIRE_RESPONSE)
    monkeypatch.setattr(machine_mod, "_post_json", recorder)
    result = extract_document_sync(
        document_path=str(page), doc_type="passport", human_review="off", api_key="k"
    )
    assert result.doc_type == "passport"


async def test_envelope(page: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wire = {
        "documents": [_WIRE_RESPONSE, _WIRE_RESPONSE],
        "envelope_confidence": 0.9,
        "cross_checks": [
            {"documents": [0, 1], "fields": ["passport_number"], "reason": "mismatch"}
        ],
        "usage": {"pages": 2, "samples": 6, "cost_cents": 50, "balance_after_cents": 900},
    }
    recorder = _Recorder(wire)
    monkeypatch.setattr(machine_mod, "_post_json", recorder)
    result = await extract_envelope(
        documents=[
            {"document_path": page, "doc_type": "passport"},
            {"document_path": page, "doc_type": "travel_declaration"},
        ],
        api_key="k",
    )
    assert result.usage.cost_cents == 50
    assert result.cross_checks[0].fields == ["passport_number"]
    assert recorder.calls[0]["url"].endswith("/api/v1/extract/envelope")


async def test_envelope_needs_two_documents(page: Path) -> None:
    with pytest.raises(VerifyDocumentArgError):
        await extract_envelope(
            documents=[{"document_path": page, "doc_type": "passport"}], api_key="k"
        )


def test_public_exports() -> None:
    import awaithumans

    assert awaithumans.extract_document is extract_document
    assert awaithumans.awaitExtract is extract_document
    assert awaithumans.ExtractionResult is ExtractionResult


_REVIEW_RESPONSE: dict[str, Any] = {
    **_WIRE_RESPONSE,
    "fields": {
        **_WIRE_RESPONSE["fields"],
        "surname": {
            "path": "surname",
            "confidence": 0.4,
            "agreement": 0.4,
            "flags": ["LOW_AGREEMENT", "PENDING_HUMAN_REVIEW", "PROVISIONAL_CALIBRATION"],
        },
    },
    "review": {"task_id": "task-77", "fields": ["surname"]},
}


class _PolledTask:
    def __init__(self, status: str, response_json: str | None) -> None:
        self.task_id = "task-77"
        self.status = status
        self.response_json = response_json


async def test_review_polled_and_merged(page: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from awaithumans.awaitverify import _managed_client

    monkeypatch.setattr(machine_mod, "_post_json", _Recorder(_REVIEW_RESPONSE))
    polls: list[str] = []

    async def fake_poll(**kwargs: Any) -> _PolledTask:
        polls.append(kwargs["task_id"])
        if len(polls) < 2:
            return _PolledTask("awaiting_review", None)
        return _PolledTask("completed", json.dumps({"surname": "De Bruijn-Corrected"}))

    monkeypatch.setattr(_managed_client, "poll_task", fake_poll)
    result = await extract_document(
        document_path=page, doc_type="passport", api_key="k"
    )
    assert polls == ["task-77", "task-77"]
    assert result.data["surname"] == "De Bruijn-Corrected"
    entry = result.fields["surname"]
    assert entry.confidence == 0.99
    assert "HUMAN_VERIFIED" in entry.flags
    assert "PENDING_HUMAN_REVIEW" not in entry.flags


async def test_review_wait_timeout_returns_machine_values(
    page: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from awaithumans.awaitverify import _managed_client

    monkeypatch.setattr(machine_mod, "_post_json", _Recorder(_REVIEW_RESPONSE))

    async def never_done(**kwargs: Any) -> _PolledTask:
        return _PolledTask("awaiting_review", None)

    monkeypatch.setattr(_managed_client, "poll_task", never_done)
    result = await extract_document(
        document_path=page,
        doc_type="passport",
        api_key="k",
        review_wait_seconds=0.01,
    )
    assert "PENDING_HUMAN_REVIEW" in result.fields["surname"].flags
    assert result.review is not None and result.review.task_id == "task-77"


async def test_machine_off_skips_polling(page: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(machine_mod, "_post_json", _Recorder(_WIRE_RESPONSE))
    result = await extract_document(
        document_path=page, doc_type="passport", human_review="off", api_key="k"
    )
    assert result.review is None
