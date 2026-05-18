"""Shared routing parser for `notify=[...]` entries.

Developers put strings in the task's `notify` list to tell awaithumans
where to send a review request:

    notify = [
        "email:alice@example.com",            # default identity / workspace
        "email+acme-prod:bob@example.com",    # identity "acme-prod"
        "slack:#approvals",                   # default Slack workspace
        "slack+T123456:#approvals",           # workspace T123456
    ]

Form:

    <channel>[+<identity>]:<target>

- `channel` is the channel type — "email" or "slack".
- `identity` (optional) picks a specific sender identity or workspace
  installation. Without it, the channel's notifier uses its default
  (env-configured sender for email, env-or-single-install workspace
  for Slack).
- `target` is everything after the first `:` — the email address, the
  Slack channel name, the user ID, etc.

`+` was chosen as the identity separator because it doesn't appear on
the left of the first `:` in any natural routing string. It may appear
inside the target (email +tag addresses, Slack channel names); that's
fine — we only look for `+` in the prefix portion.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelRoute:
    channel: str  # "email", "slack"
    identity: str | None  # identity slug / team_id; None = use default
    target: str  # email address, "#channel", "@UUSERID", etc.


def parse_route(entry: str) -> ChannelRoute | None:
    """Parse one `notify=` string into a ChannelRoute.

    Returns None if the string doesn't match the expected format —
    callers treat a None as "skip, log a warning".
    """
    if ":" not in entry:
        return None

    prefix, _, target = entry.partition(":")
    if not prefix or not target:
        return None

    if "+" in prefix:
        channel, _, identity = prefix.partition("+")
        if not channel or not identity:
            return None
    else:
        channel, identity = prefix, None

    return ChannelRoute(
        channel=channel.strip(),
        identity=identity.strip() if identity else None,
        target=target.strip(),
    )


def routes_for_channel(notify: list[str] | None, channel: str) -> list[ChannelRoute]:
    """Filter notify entries to just one channel."""
    if not notify:
        return []
    out: list[ChannelRoute] = []
    for entry in notify:
        route = parse_route(entry)
        if route is not None and route.channel == channel:
            out.append(route)
    return out
