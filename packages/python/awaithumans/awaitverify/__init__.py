"""AwaitVerify — managed document verification on top of awaithumans."""

from __future__ import annotations

from awaithumans.awaitverify.client import VerifyTimeoutRangeError, verify_document
from awaithumans.awaitverify.errors import (
    VerifyDepsMissingError,
    VerifyDocumentArgError,
    VerifyDocumentLoadError,
    VerifyDocumentTooLargeError,
    VerifyError,
)
from awaithumans.awaitverify.extraction import (
    ExtractionFailedError,
    ProviderNotSupportedError,
)
from awaithumans.awaitverify.fragmentation import fragment_document, load_pages
from awaithumans.awaitverify.types import ManagedAssignment, Priority

__all__ = [
    "verify_document",
    "ManagedAssignment",
    "Priority",
    "fragment_document",
    "load_pages",
    "VerifyError",
    "VerifyDocumentArgError",
    "VerifyDocumentTooLargeError",
    "VerifyDepsMissingError",
    "VerifyDocumentLoadError",
    "VerifyTimeoutRangeError",
    "ExtractionFailedError",
    "ProviderNotSupportedError",
]
