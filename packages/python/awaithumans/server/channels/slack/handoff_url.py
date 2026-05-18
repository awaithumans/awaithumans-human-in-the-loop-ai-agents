"""Build the dashboard URL we put in Slack messages.

The notifier and the claim-flow `chat.update` both need to drop a
"go open the task" URL into Block Kit. For Slack-only users, that URL
must double as a sign-in handshake — see `core/slack_handoff.py`. The
signing logic and the URL assembly are split so notifier code calls
one helper and doesn't think about HMACs.

When we don't have a directory user (broadcast pre-claim), there's
nobody to sign for, so we fall back to the unsigned URL. Anyone who
clicks it hits the regular login flow — operators and reviewers with
passwords get in; Slack-only users bounce to the claim-then-sign
flow instead.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

from awaithumans.server.channels.slack.handoff_url_types import HandoffParams
from awaithumans.server.core.config import settings
from awaithumans.server.core.slack_handoff import sign_handoff
from awaithumans.utils.time import to_utc_unix


def _unsigned_url(task_id: str) -> str:
    return f"{settings.PUBLIC_URL.rstrip('/')}/task?id={task_id}"


def build_review_url(*, task_id: str, params: HandoffParams | None) -> str:
    """Return a dashboard URL for the given task.

    When `params` is provided, mints a Slack-handoff URL the recipient
    can use to log in. When it's None (broadcast pre-claim), returns
    the unsigned task URL so password-equipped users can still get
    there via the login form."""
    if params is None:
        return _unsigned_url(task_id)

    sig = sign_handoff(user_id=params.user_id, task_id=task_id, exp_unix=params.exp_unix)
    qs = urllib.parse.urlencode({"u": params.user_id, "t": task_id, "e": params.exp_unix, "s": sig})
    return f"{settings.PUBLIC_URL.rstrip('/')}/api/auth/slack-handoff?{qs}"


def task_handoff_expiry(timeout_at: datetime) -> int:
    """Return the Unix timestamp the handoff URL should stop accepting.

    We bind the URL's expiry to the task's own deadline so a 7-day
    approval still has a working link on day 6. Using `task.timeout_at`
    directly keeps the contract simple: link dies with the task."""
    return to_utc_unix(timeout_at)
