"""
awaithumans — The human layer for AI agents.

Your agents already await promises. Now they can await humans.

Quick start (recommended pattern — initialize once):

    from awaithumans import AwaitHumans
    from awaithumans.providers import OpenAI

    client = AwaitHumans(
        api_key="ah_sk_live_...",
        openai=OpenAI(api_key="sk-..."),  # only needed for Flow B
    )

    result = await client.verify_document(
        task_description="Confirm the codes in column 2.",
        response_schema=VerifiedTable,
        document_path="./drawing.pdf",   # or "https://..." URL
        prior_extraction=YourPydanticModel(...),  # Flow A
    )

Three flows are supported via optional arguments:

  Flow A — Human Only (Bring Your Own Stack)
      pass prior_extraction=YourPydanticModel(...)

  Flow B — Model Then Human (Bring Your Own Model)
      pass extraction=OpenAIExtraction(model="gpt-5", prompt="...")

  Flow C — Human Then Model (AI verifier loop)
      pass verifier=...

Supported document formats:

  Direct:
    PDF, PNG, JPEG, TIFF, BMP, WEBP, GIF
  Via LibreOffice headless conversion (system binary required):
    DOCX, XLSX, PPTX, DOC, XLS, PPT, ODT, ODS, ODP, RTF
"""

from __future__ import annotations

__version__ = "0.1.10"

from awaithumans.awaitverify.types import ManagedAssignment, Priority
from awaithumans.client import await_human, await_human_sync
from awaithumans.embed import EmbedTokenResult, embed_token, embed_token_sync
from awaithumans.errors import (
    AwaitHumansError,
    MarketplaceNotAvailableError,
    PollError,
    SchemaValidationError,
    ServerUnreachableError,
    TaskAlreadyTerminalError,
    TaskCancelledError,
    TaskCreateError,
    TaskNotFoundError,
    TaskTimeoutError,
    TimeoutRangeError,
    VerificationExhaustedError,
)
from awaithumans.instance import AwaitHumans, get_default_client, set_default_client
from awaithumans.types import (
    AssignTo,
    AwaitHumanOptions,
    HumanIdentity,
    TaskRecord,
    TaskStatus,
    VerificationContext,
    VerifierConfig,
    VerifierResult,
)

# camelCase aliases per Pillar 12 rev 3 dual-naming. Same function
# objects, second name.
awaitHuman = await_human  # noqa: N816
awaitHumanSync = await_human_sync  # noqa: N816


async def verify_document(**kwargs: Any) -> Any:
    """Module-level shim — uses the lazy default client.

    For production code, prefer the explicit pattern:

        client = AwaitHumans(api_key="...", openai=OpenAI(api_key="..."))
        await client.verify_document(...)
    """
    return await get_default_client().verify_document(**kwargs)


def verify_document_sync(**kwargs: Any) -> Any:
    """Sync shim — uses the lazy default client."""
    return get_default_client().verify_document_sync(**kwargs)


# camelCase aliases for the verify shims
awaitVerify = verify_document  # noqa: N816
awaitVerifySync = verify_document_sync  # noqa: N816


async def await_review(**kwargs: Any) -> Any:
    """Module-level shim — uses the lazy default client.

    No-document text/data review: route a human judgment with just a
    task description and a response schema, no file required.
    """
    return await get_default_client().await_review(**kwargs)


def await_review_sync(**kwargs: Any) -> Any:
    """Sync shim — uses the lazy default client."""
    return get_default_client().await_review_sync(**kwargs)


# camelCase aliases for the review shims
awaitReview = await_review  # noqa: N816
awaitReviewSync = await_review_sync  # noqa: N816


# Module-level type re-export needed by the shim's signature above
from typing import Any  # noqa: E402

__all__ = [
    # client class + default-client controls
    "AwaitHumans",
    "get_default_client",
    "set_default_client",
    # core function (snake + camel)
    "await_human",
    "await_human_sync",
    "awaitHuman",
    "awaitHumanSync",
    # AwaitVerify (snake + camel)
    "verify_document",
    "verify_document_sync",
    "awaitVerify",
    "awaitVerifySync",
    # AwaitReview — no-document text/data review (snake + camel)
    "await_review",
    "await_review_sync",
    "awaitReview",
    "awaitReviewSync",
    # AwaitVerify types
    "Priority",
    "ManagedAssignment",
    # embed tokens
    "embed_token",
    "embed_token_sync",
    "EmbedTokenResult",
    # core types
    "AssignTo",
    "AwaitHumanOptions",
    "HumanIdentity",
    "TaskRecord",
    "TaskStatus",
    "VerificationContext",
    "VerifierConfig",
    "VerifierResult",
    # errors
    "AwaitHumansError",
    "MarketplaceNotAvailableError",
    "PollError",
    "SchemaValidationError",
    "ServerUnreachableError",
    "TaskAlreadyTerminalError",
    "TaskCancelledError",
    "TaskCreateError",
    "TaskNotFoundError",
    "TaskTimeoutError",
    "TimeoutRangeError",
    "VerificationExhaustedError",
]
