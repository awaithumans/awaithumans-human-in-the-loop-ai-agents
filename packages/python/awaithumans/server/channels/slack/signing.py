"""Slack request signature verification.

Slack signs every request to your interactivity webhook with an HMAC-SHA256
over the request body and timestamp:

    signature = v0=HMAC_SHA256(
        key=SLACK_SIGNING_SECRET,
        msg=f"v0:{X-Slack-Request-Timestamp}:{raw_body}",
    )

We verify the signature AND reject requests older than 5 minutes to
prevent replay attacks. Both checks are required.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from awaithumans.utils.constants import SLACK_SIGNATURE_MAX_AGE_SECONDS

logger = logging.getLogger("awaithumans.server.channels.slack.signing")


def verify_signature(
    *,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    signing_secret: str,
) -> bool:
    """Verify a Slack request signature.

    Returns False (not raise) on any failure — the caller decides what to do.
    Missing headers, stale timestamp, and invalid signature all return False.
    """
    if not timestamp or not signature or not signing_secret:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        logger.warning("Slack signature: non-integer timestamp: %s", timestamp)
        return False

    # Reject stale requests — prevents replay attacks.
    if abs(time.time() - ts_int) > SLACK_SIGNATURE_MAX_AGE_SECONDS:
        logger.warning(
            "Slack signature: stale timestamp (age=%ds, max=%ds)",
            int(abs(time.time() - ts_int)),
            SLACK_SIGNATURE_MAX_AGE_SECONDS,
        )
        return False

    basestring = b"v0:" + timestamp.encode() + b":" + body
    expected = (
        "v0="
        + hmac.new(
            signing_secret.encode(),
            basestring,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, signature)
