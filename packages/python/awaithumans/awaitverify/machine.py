"""extract_document — AwaitVerify machine extraction.

One call: a document page image in, typed fields plus a per-field
confidence score out. No fragmentation, no client-side encryption,
no extra dependencies — the page image goes to the managed backend
over TLS, is processed ephemerally, and is deleted at response time.

    from awaithumans import extract_document

    result = await extract_document(
        document_path="./passport.png",
        doc_type="passport",
        human_review="off",
    )
    result.data["passport_number"]            # extracted value (or None)
    result.fields["passport_number"].confidence

``human_review="low_confidence"`` (the default) blocks until an
AwaitVerify reviewer verifies the low-confidence fields — the
default timeout is sized for that. ``"off"`` is machine-fast.

Confidence contract: while ``result.calibration.calibrated`` is
False the scores are provisional rank-orderings (every field carries
the PROVISIONAL_CALIBRATION flag), not probabilities. Don't build
hard thresholds on provisional scores.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from awaithumans.awaitverify._managed_client import _post_json
from awaithumans.awaitverify.errors import (
    VerifyDocumentArgError,
    VerifyDocumentLoadError,
)

logger = logging.getLogger("awaithumans.awaitverify.machine")

_DEFAULT_MANAGED_URL = "https://api.awaithumans.dev"
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

_MACHINE_TIMEOUT_SECONDS = 180.0
# With human_review on, the POST returns immediately with a pending
# review handle; the SDK then long-polls the task endpoint (25s
# increments) and merges reviewer values client-side. This is how
# long we keep polling before returning machine values with the
# PENDING_HUMAN_REVIEW flags intact.
_REVIEW_WAIT_SECONDS = 1200.0
_POLL_INCREMENT_SECONDS = 25
_HUMAN_VERIFIED_CONFIDENCE = 0.99
_TERMINAL_STATUSES = {"completed", "timed_out", "cancelled"}


class ExtractedFieldConfidence(BaseModel):
    path: str
    confidence: float
    agreement: float
    flags: list[str] = Field(default_factory=list)


class ExtractionCalibration(BaseModel):
    version: str
    calibrated: bool


class MachineExtractionUsage(BaseModel):
    pages: int
    samples: int
    cost_cents: int = 0
    balance_after_cents: int | None = None


class PendingReviewInfo(BaseModel):
    task_id: str
    fields: list[str]


class ExtractionResult(BaseModel):
    """Typed mirror of the managed backend's extraction response.

    ``review`` is set when human review was requested: normally the
    SDK has already polled and merged by the time you hold this
    object, and merged fields carry HUMAN_VERIFIED. If the review
    wait timed out, the listed fields still carry
    PENDING_HUMAN_REVIEW and hold their machine values.
    """

    data: dict[str, Any]
    fields: dict[str, ExtractedFieldConfidence]
    document_confidence: float
    doc_type: str
    calibration: ExtractionCalibration
    pages: int
    usage: MachineExtractionUsage
    review: PendingReviewInfo | None = None


class EnvelopeCrossCheck(BaseModel):
    documents: list[int]
    fields: list[str]
    reason: str


class EnvelopeResult(BaseModel):
    documents: list[ExtractionResult]
    envelope_confidence: float
    cross_checks: list[EnvelopeCrossCheck]
    usage: MachineExtractionUsage


def _resolve_base(managed_url: str | None) -> str:
    return (
        managed_url or os.environ.get("AWAITHUMANS_MANAGED_URL") or _DEFAULT_MANAGED_URL
    ).rstrip("/")


def _resolve_key(api_key: str | None) -> str | None:
    return api_key or os.environ.get("AWAITHUMANS_API_KEY")


async def _load_document(
    *,
    document_path: str | Path | None,
    document_url: str | None,
    media_type: str | None,
) -> tuple[bytes, str]:
    if (document_path is None) == (document_url is None):
        raise VerifyDocumentArgError(
            "Pass exactly one of document_path= or document_url=. "
            "extract_document sends one page image (PNG or JPEG) per call."
        )
    if document_path is not None:
        path = Path(document_path)
        suffix = path.suffix.lower()
        resolved = media_type or _MEDIA_TYPES.get(suffix)
        if resolved is None:
            raise VerifyDocumentArgError(
                f"Unsupported file type {suffix!r} for machine extraction — send a "
                "PNG or JPEG page image (rasterize PDFs one page at a time), or pass "
                "media_type= explicitly."
            )
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise VerifyDocumentLoadError(f"{path}: {exc}") from exc
    else:
        assert document_url is not None
        resolved = media_type or _MEDIA_TYPES.get(Path(document_url.split("?")[0]).suffix.lower())
        if resolved is None:
            raise VerifyDocumentArgError(
                "Could not infer the image type from document_url — pass "
                'media_type="image/png" or "image/jpeg" explicitly.'
            )
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(document_url)
                resp.raise_for_status()
                payload = resp.content
        except httpx.HTTPError as exc:
            raise VerifyDocumentLoadError(f"{document_url}: {exc}") from exc
    if len(payload) > _MAX_IMAGE_BYTES:
        raise VerifyDocumentArgError(
            f"The page image is {len(payload)} bytes; machine extraction accepts "
            f"up to {_MAX_IMAGE_BYTES} ({_MAX_IMAGE_BYTES // (1024 * 1024)} MB). "
            "Downscale the image or send one page at a time."
        )
    return payload, resolved


async def _merge_review(
    result: ExtractionResult,
    *,
    api_key: str | None,
    base: str,
    review_wait_seconds: float,
) -> ExtractionResult:
    """Poll the review task and merge reviewer values client-side."""
    import json as _json
    import time as _time

    from awaithumans.awaitverify._managed_client import poll_task  # noqa: PLC0415

    assert result.review is not None
    deadline = _time.monotonic() + review_wait_seconds
    while _time.monotonic() < deadline:
        polled = await poll_task(
            managed_url=base,
            api_key=api_key,
            task_id=result.review.task_id,
            timeout_seconds=_POLL_INCREMENT_SECONDS,
        )
        if polled.status not in _TERMINAL_STATUSES:
            continue
        if polled.status == "completed" and polled.response_json:
            try:
                human_values = _json.loads(polled.response_json)
            except ValueError:
                return result
            if not isinstance(human_values, dict):
                return result
            for path in result.review.fields:
                entry = result.fields.get(path)
                if entry is None:
                    continue
                if path in human_values:
                    result.data[path] = human_values[path]
                entry.confidence = _HUMAN_VERIFIED_CONFIDENCE
                entry.flags = [
                    f
                    for f in entry.flags
                    if f not in ("PENDING_HUMAN_REVIEW", "NOT_FOUND", "LOW_AGREEMENT")
                ]
                entry.flags.append("HUMAN_VERIFIED")
            result.document_confidence = round(
                sum(e.confidence for e in result.fields.values())
                / max(len(result.fields), 1),
                4,
            )
        return result
    logger.warning(
        "Human review still pending after %.0fs (task=%s) — returning machine "
        "values; the listed fields keep PENDING_HUMAN_REVIEW.",
        review_wait_seconds,
        result.review.task_id,
    )
    return result


async def extract_document(
    *,
    document_path: str | Path | None = None,
    document_url: str | None = None,
    doc_type: str | None = None,
    response_schema: dict[str, Any] | None = None,
    human_review: str = "low_confidence",
    media_type: str | None = None,
    api_key: str | None = None,
    managed_url: str | None = None,
    timeout_seconds: float | None = None,
    review_wait_seconds: float = _REVIEW_WAIT_SECONDS,
) -> ExtractionResult:
    """Machine-extract one document page via the managed backend.

    Args:
        document_path / document_url: exactly one — the page image
            (PNG or JPEG, max 10 MB).
        doc_type / response_schema: exactly one — a built-in document
            type ("passport", "travel_ticket", "airport_stamp",
            "proof_of_profile", "travel_declaration") or your own
            schema ({"name": ..., "fields": [{"name", "type"}]}).
        human_review: "off" (machine only), "low_confidence"
            (default — reviewers verify low-confidence fields; the
            SDK polls and merges their answers before returning), or
            "all".
        review_wait_seconds: how long to wait for human review
            before returning machine values with PENDING_HUMAN_REVIEW
            flags (poll continues in 25s increments until then).
        api_key / managed_url: fall back to AWAITHUMANS_API_KEY /
            AWAITHUMANS_MANAGED_URL.
    """
    if (doc_type is None) == (response_schema is None):
        raise VerifyDocumentArgError(
            "Pass exactly one of doc_type= (a built-in document type) or "
            "response_schema= (your own field spec)."
        )
    payload, resolved_media = await _load_document(
        document_path=document_path, document_url=document_url, media_type=media_type
    )
    body: dict[str, Any] = {
        "document_base64": base64.standard_b64encode(payload).decode("ascii"),
        "media_type": resolved_media,
        "human_review": human_review,
    }
    if doc_type is not None:
        body["doc_type"] = doc_type
    else:
        body["response_schema"] = response_schema

    base = _resolve_base(managed_url)
    key = _resolve_key(api_key)
    raw = await _post_json(
        url=f"{base}/api/v1/extract",
        body=body,
        api_key=key,
        http_timeout_seconds=timeout_seconds or _MACHINE_TIMEOUT_SECONDS,
    )
    result = ExtractionResult.model_validate(raw)
    if result.review is not None and review_wait_seconds > 0:
        result = await _merge_review(
            result, api_key=key, base=base, review_wait_seconds=review_wait_seconds
        )
    return result


def extract_document_sync(**kwargs: Any) -> ExtractionResult:
    return asyncio.run(extract_document(**kwargs))


async def extract_envelope(
    *,
    documents: list[dict[str, Any]],
    api_key: str | None = None,
    managed_url: str | None = None,
    timeout_seconds: float | None = None,
) -> EnvelopeResult:
    """Extract one applicant's documents together with cross-document
    consistency checks (machine-only).

    Each entry: {"document_path": ... or "document_url": ...,
    "doc_type": ..., optional "media_type": ...}.
    """
    if len(documents) < 2:
        raise VerifyDocumentArgError(
            "extract_envelope needs at least two documents — for a single "
            "document use extract_document."
        )
    wire_docs: list[dict[str, Any]] = []
    for spec in documents:
        if "doc_type" not in spec:
            raise VerifyDocumentArgError(
                "Every envelope document needs a doc_type (built-in types only)."
            )
        payload, resolved_media = await _load_document(
            document_path=spec.get("document_path"),
            document_url=spec.get("document_url"),
            media_type=spec.get("media_type"),
        )
        wire_docs.append(
            {
                "document_base64": base64.standard_b64encode(payload).decode("ascii"),
                "media_type": resolved_media,
                "doc_type": spec["doc_type"],
            }
        )
    raw = await _post_json(
        url=f"{_resolve_base(managed_url)}/api/v1/extract/envelope",
        body={"documents": wire_docs},
        api_key=_resolve_key(api_key),
        http_timeout_seconds=timeout_seconds or _MACHINE_TIMEOUT_SECONDS * 2,
    )
    return EnvelopeResult.model_validate(raw)


def extract_envelope_sync(**kwargs: Any) -> EnvelopeResult:
    return asyncio.run(extract_envelope(**kwargs))


# CamelCase aliases per the Await* naming convention.
awaitExtract = extract_document  # noqa: N816
awaitExtractSync = extract_document_sync  # noqa: N816
