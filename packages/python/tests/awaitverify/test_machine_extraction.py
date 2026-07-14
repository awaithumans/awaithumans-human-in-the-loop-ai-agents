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


async def test_human_review_default_and_timeout(
    page: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(_WIRE_RESPONSE)
    monkeypatch.setattr(machine_mod, "_post_json", recorder)
    await extract_document(document_path=page, doc_type="passport", api_key="k")
    call = recorder.calls[0]
    assert call["body"]["human_review"] == "low_confidence"
    # Blocking review gets the long timeout automatically.
    assert call["http_timeout_seconds"] == machine_mod._HUMAN_REVIEW_TIMEOUT_SECONDS


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
