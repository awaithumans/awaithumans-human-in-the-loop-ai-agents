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

# `\Z` anchors at end-of-string (unlike `$`, which matches before a
# trailing newline). `re.fullmatch` also requires start-of-string. The
# combo closes the `"gmail.com\n"` deny-list bypass that `^...$` allows.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")


def validate_demo_email(email: str, *, allowlist_extra: frozenset[str]) -> None:
    """Raise InvalidDemoEmailError unless `email` is a syntactically
    valid address on a non-free, non-disposable domain (or on a domain
    explicitly allowlisted). Surrounding whitespace is tolerated so a
    pasted address doesn't fail on a stray space or newline."""
    cleaned = email.strip()
    if not _EMAIL_RE.fullmatch(cleaned):
        raise InvalidDemoEmailError()
    domain = cleaned.rsplit("@", 1)[1].lower()
    if domain in allowlist_extra:
        return
    if domain in DEMO_FREE_EMAIL_DOMAINS or domain in DEMO_DISPOSABLE_EMAIL_DOMAINS:
        raise InvalidDemoEmailError()
