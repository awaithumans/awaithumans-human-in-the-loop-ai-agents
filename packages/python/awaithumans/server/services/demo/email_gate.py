"""Demo email allow/deny gate.

Reject free providers (gmail, yahoo, etc.) and disposable domains. The
allowlist_extra parameter overrides the deny list for one-off
whitelisting (e.g. a Fortune 500 contact whose company runs Workspace
on a custom domain that happens to look like a free provider).
"""

from __future__ import annotations

import re

from awaithumans.server.services.demo.exceptions import InvalidDemoEmailError
from awaithumans.utils.constants import (
    DEMO_DISPOSABLE_EMAIL_DOMAINS,
    DEMO_FREE_EMAIL_DOMAINS,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_demo_email(email: str, *, allowlist_extra: frozenset[str]) -> None:
    """Raise InvalidDemoEmailError unless `email` is a syntactically
    valid address on a non-free, non-disposable domain (or on a
    domain explicitly allowlisted)."""
    if not _EMAIL_RE.match(email):
        raise InvalidDemoEmailError()
    domain = email.rsplit("@", 1)[1].lower()
    if domain in allowlist_extra:
        return
    if domain in DEMO_FREE_EMAIL_DOMAINS or domain in DEMO_DISPOSABLE_EMAIL_DOMAINS:
        raise InvalidDemoEmailError()
