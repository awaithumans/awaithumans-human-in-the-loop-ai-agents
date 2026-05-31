"""verify_document — AwaitVerify managed document verification entrypoint.

Three flows, one function. The flow is implicit in which arguments
you pass:

  Flow A — Human Only (Bring Your Own Stack)
      pass prior_extraction=YourPydanticModel(...)

  Flow B — Model Then Human (Bring Your Own Model)
      pass extraction=OpenAIExtraction(model=..., prompt=...)

  Flow C — Human Then Model (AI verifier loop)
      pass verifier=...

Internally the SDK now calls the AwaitVerify managed backend:

  1. POST /api/v1/awaitverify/uploads — get DEK + signed URLs
  2. Client-side: encrypt each fragment with AES-256-GCM(DEK)
  3. PUT each ciphertext fragment to its signed URL
  4. POST /api/v1/awaitverify/tasks — create the verification task
  5. GET /api/v1/awaitverify/tasks/{id}/poll — wait for completion
  6. Validate response against response_schema and return it typed
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
from pydantic import BaseModel

from awaithumans.awaitverify._encryption import encrypt_fragment
from awaithumans.awaitverify._managed_client import (
    ManagedBackendError,
)
from awaithumans.awaitverify._managed_client import (
    create_task as _managed_create_task,
)
from awaithumans.awaitverify._managed_client import (
    create_upload_session as _managed_create_upload_session,
)
from awaithumans.awaitverify._managed_client import (
    poll_task as _managed_poll_task,
)
from awaithumans.awaitverify._managed_client import (
    upload_fragment as _managed_upload_fragment,
)
from awaithumans.awaitverify.errors import (
    VerifyDocumentArgError,
    VerifyDocumentLoadError,
    VerifyError,
)
from awaithumans.awaitverify.extraction import run_extraction
from awaithumans.awaitverify.fragmentation import fragment_document
from awaithumans.awaitverify.types import Priority
from awaithumans.utils.constants import (
    AWAITVERIFY_DEFAULT_TIMEOUT_SECONDS,
    AWAITVERIFY_FRAGMENT_COUNT,
    AWAITVERIFY_MIN_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from awaithumans.instance import AwaitHumans
    from awaithumans.providers import ExtractionConfig
    from awaithumans.types import VerifierConfig

logger = logging.getLogger("awaithumans.awaitverify")

T = TypeVar("T", bound=BaseModel)

# Long-poll step length used when waiting on the managed backend.
# Each poll holds the connection for ~25s, then reconnects. Mirrors
# the OSS server's pattern.
_POLL_STEP_SECONDS = 25


class VerifyTimeoutRangeError(VerifyError):
    """`timeout_seconds` is outside the AwaitVerify allowed range."""

    def __init__(self, value: int) -> None:
        super().__init__(
            code="VERIFY_TIMEOUT_OUT_OF_RANGE",
            message=(
                f"timeout_seconds must be between "
                f"{AWAITVERIFY_MIN_TIMEOUT_SECONDS} (24 hours) and "
                f"{MAX_TIMEOUT_SECONDS} (30 days). Got: {value}."
            ),
            hint=(
                "The 24-hour minimum is deliberate: our standard SLA is "
                "3-4 hours and we want generous headroom before the task is "
                "marked timed_out. To abandon the awaitable earlier locally, "
                "wrap the call in `asyncio.wait_for(client.verify_document(...), "
                "timeout=N)` — the server-side task continues either way."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/timeouts",
        )


class VerifyTaskTimeoutError(VerifyError):
    """The managed task timed out before a reviewer completed it."""

    def __init__(self, task_id: str, timeout_seconds: int) -> None:
        super().__init__(
            code="VERIFY_TASK_TIMEOUT",
            message=(f"Task {task_id!r} did not complete within {timeout_seconds} seconds."),
            hint=(
                "  - Check the AwaitVerify dashboard for the task's current state.\n"
                "  - For Express SLA, ensure the call was during business hours.\n"
                "  - If you set a custom timeout, consider raising it."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/timeouts",
        )


class VerifyTaskNonTerminalError(VerifyError):
    """Task reached a terminal but non-COMPLETED state (timed_out/cancelled)."""

    def __init__(self, task_id: str, status: str) -> None:
        super().__init__(
            code="VERIFY_TASK_NOT_COMPLETED",
            message=f"Task {task_id!r} ended with status {status!r}, not completed.",
            hint=(
                "  - timed_out: no reviewer submitted within the SLA window.\n"
                "  - cancelled: customer or operator cancelled the task.\n"
                "Check the AwaitVerify dashboard for details."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/lifecycle",
        )


async def verify_document(
    *,
    client: AwaitHumans | None = None,
    task_description: str,
    response_schema: type[T],
    document_path: str | Path | None = None,
    document_bytes: bytes | None = None,
    prior_extraction: BaseModel | None = None,
    extraction: ExtractionConfig | None = None,
    verifier: VerifierConfig | None = None,
    priority: Priority | str | None = None,
    timeout_seconds: int | None = None,
    idempotency_key: str | None = None,
    task_metadata: dict[str, str] | None = None,
) -> T:
    """Verify a document via AwaitVerify managed reviewers.

    See module docstring for the flow and `pillars/12-awaitverify.md`
    (rev 3) for product context.

    Args:
        client: AwaitHumans client. If None, uses the lazy default
            (which reads AWAITHUMANS_API_KEY / AWAITHUMANS_MANAGED_URL
            from environment).
        task_description: Plain-English instruction the reviewer sees.
        response_schema: Pydantic model the result is validated against.
        document_path: Local filesystem path OR HTTP/HTTPS URL.
            Mutually exclusive with document_bytes.
        document_bytes: Raw document bytes. Mutually exclusive with
            document_path.
        prior_extraction: Flow A — your already-extracted data, as a
            Pydantic model. The human verifies it against the fragments.
        extraction: Flow B — typed extraction config (e.g.
            OpenAIExtraction). The SDK runs the model locally; the
            output is what the human verifies.
        verifier: Flow C — VerifierConfig for an AI verifier loop on
            the human's submission.
        priority: "standard" (base rate) or "high" (Express SLA, 2×
            rate). Defaults to standard.
        timeout_seconds: Server-side task lifetime. Min 24h, max 30 days.
            Defaults to 48h.
        idempotency_key: Reserved — not yet supported in the managed
            backend (Phase 3).
        task_metadata: Free-form key-value context shown to the human
            reviewer verbatim in Slack and the dashboard. Examples:
            ``{"customer": "Acme Corp", "vertical": "construction"}``.
            Helpful when the reviewer needs domain context to make a
            judgment call. Keys/values are strings; nested structures
            are rejected by the managed backend.
    """
    if client is None:
        from awaithumans.instance import get_default_client  # noqa: PLC0415

        client = get_default_client()

    resolved_timeout = _resolve_timeout(timeout_seconds)
    resolved_priority = _resolve_priority(priority)

    if idempotency_key is not None:
        logger.warning(
            "idempotency_key is reserved for Phase 3 on the managed backend "
            "and is ignored in this call"
        )

    raw = await _load_document_bytes(
        document_path=document_path,
        document_bytes=document_bytes,
    )

    # ── Flow B: optionally run customer-provided model first ────────
    flow_b_output: dict[str, Any] | None = None
    if extraction is not None:
        if prior_extraction is not None:
            raise VerifyDocumentArgError(
                "Pass either `prior_extraction` (Flow A) or `extraction` (Flow B), not both."
            )
        flow_b_output = await run_extraction(
            document_bytes=raw,
            extraction=extraction,
            response_schema=response_schema,
            client=client,
        )

    # Resolve the chosen extraction. Flow A and Flow B are mutually
    # exclusive (the combo is rejected at lines 208-211 above), so at
    # most one of these is non-None. Flow A delivers a Pydantic model
    # — `model_dump(mode="json")` so nested datetime/UUID/Enum values
    # serialize through to JSON cleanly. Flow B's `flow_b_output` is
    # already a dict from `run_extraction()`, validated against
    # response_schema there.
    #
    # The managed backend validates `initial_response` against the
    # task's `response_schema` before forwarding to the OSS reviewer
    # dashboard, which mounts the form with the values pre-populated
    # so the reviewer verifies/corrects rather than re-types.
    initial_response: dict[str, Any] | None
    if prior_extraction is not None:
        initial_response = prior_extraction.model_dump(mode="json")
    elif flow_b_output is not None:
        initial_response = flow_b_output
    else:
        initial_response = None

    # ── Fragment client-side ────────────────────────────────────────
    page_fragments = fragment_document(raw)
    page_count = len(page_fragments)
    logger.info(
        "AwaitVerify: %d pages × %d fragments, flow_b=%s, flow_c=%s, priority=%s",
        page_count,
        AWAITVERIFY_FRAGMENT_COUNT,
        flow_b_output is not None,
        verifier is not None,
        resolved_priority,
    )

    # ── 1. POST /uploads → mint signed URLs + DEK ───────────────────
    session = await _managed_create_upload_session(
        managed_url=client.managed_url,
        api_key=client.api_key,
        page_count=page_count,
        content_type="image/png",
    )

    # ── 2 + 3. SDK-side encrypt + PUT each fragment ─────────────────
    # Fragments arrive grouped by page from fragment_document; the
    # managed backend issues one slot per (page_index, fragment_index)
    # pair in matching order. Verify by index in case of skew.
    slot_by_key = {(s.page_index, s.fragment_index): s for s in session.fragments}

    upload_tasks: list[Any] = []
    for page_index, fragments_for_page in enumerate(page_fragments):
        for fragment_index, plaintext in enumerate(fragments_for_page):
            slot = slot_by_key.get((page_index, fragment_index))
            if slot is None:
                raise VerifyError(
                    code="MANAGED_BACKEND_SHAPE_MISMATCH",
                    message=(
                        f"Managed backend did not return a slot for page "
                        f"{page_index} fragment {fragment_index}."
                    ),
                    hint=(
                        "This indicates a server-side bug or a version "
                        "skew between the SDK and the managed backend. "
                        "File an issue with the slot list shape."
                    ),
                    docs_url="https://docs.awaithumans.dev/awaitverify/errors",
                )
            ciphertext = encrypt_fragment(plaintext, session.dek)
            upload_tasks.append(_managed_upload_fragment(slot=slot, ciphertext=ciphertext))

    # Upload all fragments in parallel — they're independent.
    await asyncio.gather(*upload_tasks)

    # Capture the upload session id before forgetting the DEK.
    upload_session_id = session.upload_session_id
    session_dek_len = len(session.dek)  # for log only
    # SDK forgets the plaintext DEK now. Server has only the wrapped
    # form, which it destroys on reviewer submit.

    # ── 4. POST /tasks ──────────────────────────────────────────────
    response_schema_json = json.dumps(response_schema.model_json_schema())
    task = await _managed_create_task(
        managed_url=client.managed_url,
        api_key=client.api_key,
        upload_session_id=upload_session_id,
        task_description=task_description,
        response_schema_json=response_schema_json,
        priority=resolved_priority.value,
        task_metadata=task_metadata,
        initial_response=initial_response,
    )

    logger.info(
        "AwaitVerify task created: id=%s (dek_bytes=%d, timeout=%ds)",
        task.task_id,
        session_dek_len,
        resolved_timeout,
    )

    # ── 5. Poll until terminal ──────────────────────────────────────
    deadline = asyncio.get_event_loop().time() + resolved_timeout
    while True:
        polled = await _managed_poll_task(
            managed_url=client.managed_url,
            api_key=client.api_key,
            task_id=task.task_id,
            timeout_seconds=_POLL_STEP_SECONDS,
        )
        if polled.status == "completed":
            break
        if polled.status in ("timed_out", "cancelled"):
            raise VerifyTaskNonTerminalError(task.task_id, polled.status)
        # status == "awaiting_review" → loop
        if asyncio.get_event_loop().time() >= deadline:
            raise VerifyTaskTimeoutError(task.task_id, resolved_timeout)

    if polled.response_json is None:
        raise VerifyError(
            code="VERIFY_TASK_COMPLETED_WITHOUT_RESPONSE",
            message=f"Task {task.task_id} is completed but has no response body.",
            hint="This indicates a managed backend bug. Please file an issue.",
            docs_url="https://docs.awaithumans.dev/awaitverify/errors",
        )

    # ── 6. Validate against response_schema and return ──────────────
    try:
        parsed = json.loads(polled.response_json)
        return response_schema.model_validate(parsed)
    except Exception as exc:
        raise VerifyError(
            code="VERIFY_RESPONSE_VALIDATION_FAILED",
            message=f"Reviewer response for task {task.task_id} failed schema validation.",
            hint=(
                f"Validation error: {exc}\n\n"
                "The reviewer's submission did not match response_schema. "
                "Check the AwaitVerify dashboard for the raw response."
            ),
            docs_url="https://docs.awaithumans.dev/awaitverify/errors",
        ) from exc


def _resolve_timeout(value: int | None) -> int:
    if value is None:
        return AWAITVERIFY_DEFAULT_TIMEOUT_SECONDS
    if value < AWAITVERIFY_MIN_TIMEOUT_SECONDS or value > MAX_TIMEOUT_SECONDS:
        raise VerifyTimeoutRangeError(value)
    return value


def _resolve_priority(value: Priority | str | None) -> Priority:
    if value is None:
        return Priority.STANDARD
    if isinstance(value, Priority):
        return value
    return Priority(value)


async def _load_document_bytes(
    *,
    document_path: str | Path | None,
    document_bytes: bytes | None,
) -> bytes:
    provided = sum(x is not None for x in (document_path, document_bytes))
    if provided != 1:
        raise VerifyDocumentArgError(
            "Pass exactly one of document_path=... (local path or URL) or document_bytes=...."
        )

    if document_bytes is not None:
        return document_bytes

    assert document_path is not None
    path_str = str(document_path)
    if path_str.startswith(("http://", "https://")):
        return await _fetch_url(path_str)

    try:
        return Path(document_path).read_bytes()
    except OSError as exc:
        raise VerifyDocumentLoadError(f"could not read {document_path}: {exc}") from exc


async def _fetch_url(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        try:
            resp = await http_client.get(url)
        except httpx.HTTPError as exc:
            raise VerifyDocumentLoadError(f"could not fetch {url}: {exc}") from exc
    if resp.status_code != 200:
        raise VerifyDocumentLoadError(f"fetch {url} returned HTTP {resp.status_code}")
    return resp.content


__all__ = [
    "verify_document",
    "VerifyTimeoutRangeError",
    "VerifyTaskTimeoutError",
    "VerifyTaskNonTerminalError",
    "ManagedBackendError",
]
