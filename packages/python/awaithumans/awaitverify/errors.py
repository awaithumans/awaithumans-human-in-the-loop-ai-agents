"""AwaitVerify-specific errors. Follow the what → why → fix → docs pattern.

These wrap the generic `AwaitHumansError` so callers can `except VerifyError`
and pick up everything that happens inside `verify_document`.
"""

from __future__ import annotations

from awaithumans.errors import AwaitHumansError
from awaithumans.utils.constants import (
    AWAITVERIFY_MAX_PAGES,
    DOCS_BASE_URL,
)

_VERIFY_DOCS_URL = f"{DOCS_BASE_URL}/awaitverify"


class VerifyError(AwaitHumansError):
    """Base class for AwaitVerify-specific errors. Subclass of AwaitHumansError."""


class VerifyDocumentArgError(VerifyError):
    """Caller passed an invalid combination of document source arguments."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            code="VERIFY_DOCUMENT_ARG_ERROR",
            message=detail,
            hint=(
                "Pass exactly one of:\n"
                "  - document_url=...  (the SDK fetches the URL locally)\n"
                "  - document_path=... (the SDK reads the file from disk)\n"
                "The full unfragmented document never leaves your environment "
                "in either case; the SDK fragments it client-side before upload."
            ),
            docs_url=f"{_VERIFY_DOCS_URL}/getting-started",
        )


class VerifyDocumentTooLargeError(VerifyError):
    """Document page count exceeds the v1 SDK soft cap."""

    def __init__(self, page_count: int) -> None:
        super().__init__(
            code="VERIFY_DOCUMENT_TOO_LARGE",
            message=(
                f"This document has {page_count} pages. AwaitVerify supports "
                f"up to {AWAITVERIFY_MAX_PAGES} pages per call."
            ),
            hint=(
                "Split the document into smaller chunks client-side and call "
                "verify_document once per chunk. For large-document customers, "
                "contact us about a Scale tier — we can raise the cap "
                "manually for your account."
            ),
            docs_url=f"{_VERIFY_DOCS_URL}/limits",
        )


class VerifyDepsMissingError(VerifyError):
    """The `awaithumans[awaitverify]` extra is not installed."""

    def __init__(self, missing: str) -> None:
        super().__init__(
            code="VERIFY_DEPS_MISSING",
            message=(
                f"verify_document requires the optional dependency '{missing}', "
                "which is not installed."
            ),
            hint=(
                "Install the AwaitVerify extras:\n\n"
                "    pip install 'awaithumans[awaitverify]'\n\n"
                "This pulls in pdf2image and Pillow for client-side document "
                "fragmentation. They are kept out of the base install so the "
                "core SDK stays lightweight (httpx + pydantic only)."
            ),
            docs_url=f"{_VERIFY_DOCS_URL}/install",
        )


class VerifyDocumentLoadError(VerifyError):
    """Failed to load or decode the document for fragmentation."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            code="VERIFY_DOCUMENT_LOAD_ERROR",
            message=f"Could not load the document for verification: {detail}",
            hint=(
                "Supported formats in v1: PDF, PNG, JPG/JPEG. The SDK reads "
                "the document on your machine, never on our servers.\n"
                "  - For PDFs, pdf2image requires the `poppler` system binary. "
                "On macOS: `brew install poppler`. On Debian/Ubuntu: "
                "`apt-get install poppler-utils`.\n"
                "  - If the file is encrypted or password-protected, decrypt "
                "it before passing to verify_document."
            ),
            docs_url=f"{_VERIFY_DOCS_URL}/supported-formats",
        )


__all__ = [
    "VerifyError",
    "VerifyDocumentArgError",
    "VerifyDocumentTooLargeError",
    "VerifyDepsMissingError",
    "VerifyDocumentLoadError",
]
