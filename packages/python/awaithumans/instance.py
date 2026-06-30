"""AwaitHumans client class — initialize once, reuse across calls.

Recommended entry point for production code:

    from awaithumans import AwaitHumans
    from awaithumans.providers import OpenAI

    client = AwaitHumans(
        api_key="ah_sk_live_...",
        openai=OpenAI(api_key="sk-..."),  # only needed for Flow B
    )

    result = await client.verify_document(...)

For one-off scripts the module-level shim functions (`verify_document`,
`await_human`) still work — they lazily create a default client from
environment variables.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from awaithumans.providers import (
    Anthropic,
    AzureDI,
    AzureOpenAI,
    OpenAI,
    Reducto,
)

if TYPE_CHECKING:
    from awaithumans.awaitverify.types import Priority
    from awaithumans.providers import ExtractionConfig
    from awaithumans.types import VerifierConfig


T = TypeVar("T", bound=BaseModel)


class AwaitHumans:
    """Configured client for awaithumans and AwaitVerify.

    Args:
        api_key: AwaitVerify / AwaitHumans API key. Falls back to
            AWAITHUMANS_API_KEY environment variable.
        server_url: Base URL of the AwaitHumans OSS server, used by
            `await_human` calls. Falls back to AWAITHUMANS_SERVER_URL
            or the SDK's discovery rules.
        managed_url: Base URL of the AwaitVerify managed backend, used
            by `verify_document` calls. Falls back to
            AWAITHUMANS_MANAGED_URL or the hosted default
            (`https://api.awaithumans.dev`).
        openai: Optional OpenAI credentials for Flow B. The customer's
            key is used locally; it never reaches AwaitVerify.
        anthropic: Optional Anthropic (Claude) credentials.
        azure_openai: Optional Azure OpenAI deployment credentials.
        reducto: Optional Reducto credentials for OCR-based Flow B.
        azure_di: Optional Azure Document Intelligence credentials.

    Open source OCR providers (DoclingExtraction, PaddleOCRExtraction)
    take no credentials and need no constructor arg here.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        server_url: str | None = None,
        managed_url: str | None = None,
        openai: OpenAI | None = None,
        anthropic: Anthropic | None = None,
        azure_openai: AzureOpenAI | None = None,
        reducto: Reducto | None = None,
        azure_di: AzureDI | None = None,
    ):
        self.api_key = api_key or os.environ.get("AWAITHUMANS_API_KEY")
        self.server_url = server_url or os.environ.get("AWAITHUMANS_SERVER_URL")
        # Production managed endpoint. Customers can override per
        # instance via `managed_url=` or per process via the
        # AWAITHUMANS_MANAGED_URL env var.
        self.managed_url = (
            managed_url
            or os.environ.get("AWAITHUMANS_MANAGED_URL")
            or "https://api.awaithumans.dev"
        )
        self.openai = openai
        self.anthropic = anthropic
        self.azure_openai = azure_openai
        self.reducto = reducto
        self.azure_di = azure_di

    # ── core primitive ──────────────────────────────────────────────

    async def await_human(
        self,
        *,
        task: str,
        payload_schema: type[T],
        payload: BaseModel,
        response_schema: type[T],
        timeout_seconds: int,
        assign_to: object | None = None,
        notify: list[str] | None = None,
        verifier: VerifierConfig | None = None,
        idempotency_key: str | None = None,
        redact_payload: bool = False,
    ) -> T:
        """Delegate a task to a human and await the result.

        Same semantics as the module-level `await_human`, but with
        `api_key` and `server_url` bound to this client instance.
        """
        from awaithumans.client import await_human as _await_human  # noqa: PLC0415

        return await _await_human(
            task=task,
            payload_schema=payload_schema,
            payload=payload,
            response_schema=response_schema,
            timeout_seconds=timeout_seconds,
            assign_to=assign_to,
            notify=notify,
            verifier=verifier,
            idempotency_key=idempotency_key,
            redact_payload=redact_payload,
            server_url=self.server_url,
            api_key=self.api_key,
        )

    def await_human_sync(self, **kwargs: Any) -> Any:
        return asyncio.run(self.await_human(**kwargs))

    # ── AwaitVerify managed product ─────────────────────────────────

    async def verify_document(
        self,
        *,
        task_description: str,
        response_schema: type[BaseModel],
        document_path: str | Path | None = None,
        document_bytes: bytes | None = None,
        prior_extraction: BaseModel | None = None,
        extraction: ExtractionConfig | None = None,
        verifier: VerifierConfig | None = None,
        priority: Priority | str | None = None,
        timeout_seconds: int | None = None,
        idempotency_key: str | None = None,
        task_metadata: dict[str, str] | None = None,
        upload_timeout_seconds: int | None = None,
    ) -> Any:
        """Verify a document via AwaitVerify managed reviewers.

        See the module-level `verify_document` for the full argument
        reference, including `task_metadata` for surfacing free-form
        context to the reviewer and `upload_timeout_seconds` for
        per-PUT upload tuning on flaky networks.
        """
        from awaithumans.awaitverify.client import (  # noqa: PLC0415
            verify_document as _verify,
        )

        return await _verify(
            client=self,
            task_description=task_description,
            response_schema=response_schema,
            document_path=document_path,
            document_bytes=document_bytes,
            prior_extraction=prior_extraction,
            extraction=extraction,
            verifier=verifier,
            priority=priority,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            task_metadata=task_metadata,
            upload_timeout_seconds=upload_timeout_seconds,
        )

    def verify_document_sync(self, **kwargs: Any) -> Any:
        return asyncio.run(self.verify_document(**kwargs))

    async def await_review(
        self,
        *,
        task_description: str,
        response_schema: type[BaseModel],
        task_metadata: dict[str, str] | None = None,
        priority: Priority | str | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        """Route a no-document text/data decision to a human reviewer.

        See the module-level `await_review` for the full argument
        reference. Billed as one standard page.
        """
        from awaithumans.awaitverify.client import (  # noqa: PLC0415
            await_review as _review,
        )

        return await _review(
            client=self,
            task_description=task_description,
            response_schema=response_schema,
            task_metadata=task_metadata,
            priority=priority,
            timeout_seconds=timeout_seconds,
        )

    def await_review_sync(self, **kwargs: Any) -> Any:
        return asyncio.run(self.await_review(**kwargs))

    # ── camelCase aliases (Pillar 12 rev 3 dual-naming) ─────────────

    async def awaitHuman(self, **kwargs: Any) -> Any:  # noqa: N802
        return await self.await_human(**kwargs)

    def awaitHumanSync(self, **kwargs: Any) -> Any:  # noqa: N802
        return self.await_human_sync(**kwargs)

    async def awaitVerify(self, **kwargs: Any) -> Any:  # noqa: N802
        return await self.verify_document(**kwargs)

    def awaitVerifySync(self, **kwargs: Any) -> Any:  # noqa: N802
        return self.verify_document_sync(**kwargs)

    async def awaitReview(self, **kwargs: Any) -> Any:  # noqa: N802
        return await self.await_review(**kwargs)

    def awaitReviewSync(self, **kwargs: Any) -> Any:  # noqa: N802
        return self.await_review_sync(**kwargs)


# ── Module-level default client (lazy) ──────────────────────────────

_default_client: AwaitHumans | None = None


def get_default_client() -> AwaitHumans:
    """Return the lazily-created default client.

    Used by the module-level shim functions when no explicit client
    is provided. Reads AWAITHUMANS_API_KEY / AWAITHUMANS_SERVER_URL
    from environment on first access. To configure providers
    (Flow B), construct AwaitHumans(...) explicitly.
    """
    global _default_client
    if _default_client is None:
        _default_client = AwaitHumans()
    return _default_client


def set_default_client(client: AwaitHumans) -> None:
    """Override the default client. Useful in tests and frameworks."""
    global _default_client
    _default_client = client


__all__ = [
    "AwaitHumans",
    "get_default_client",
    "set_default_client",
]
