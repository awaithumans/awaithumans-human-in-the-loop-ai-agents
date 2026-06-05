"""AwaitVerify demo tasks get a visible tag in the Slack message text.

Reviewers run a shared queue — without the tag, free demo tasks look
identical to paying ones. The hot-lane variant (warm prospects) gets a
distinct middle-dot tag so the founder can spot pings instantly.

The hot lane also overrides the destination Slack channel id via the
`_hot_lane_channel_override` helper — those tests live at the bottom of
this file so all demo-routing logic stays in one place.

These tests exercise the pure helpers directly; the integration with
the post-message flow is verified by the broader notifier e2e tests.
"""

from __future__ import annotations

import pytest

from awaithumans.server.channels.slack.notifier import (
    _demo_prefix,
    _hot_lane_channel_override,
)
from awaithumans.server.core.config import settings


def test_demo_task_gets_demo_prefix() -> None:
    assign_to = {"managed": "awaitverify", "priority": "demo"}
    assert _demo_prefix(assign_to) == "[DEMO] "


def test_demo_hot_task_gets_demo_hot_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default settings include a mention (`<!channel>`); clear it for
    # this baseline test so it asserts the bare `[DEMO·HOT] ` shape.
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_MENTION", "")
    assign_to = {"managed": "awaitverify", "priority": "demo_hot"}
    assert _demo_prefix(assign_to) == "[DEMO·HOT] "


def test_hot_demo_message_includes_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot-lane demo + configured mention → mention leads the prefix.

    Slack only parses `<!channel>` / `<@U...>` as a real ping when it
    appears at the start of the message text. The rendered title must
    therefore begin with the mention, then the `[DEMO·HOT]` tag, then
    the original task title.
    """
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_MENTION", "<!channel>")
    assign_to = {"managed": "awaitverify", "priority": "demo_hot"}
    prefix = _demo_prefix(assign_to)
    assert prefix == "<!channel> [DEMO·HOT] "
    # Sanity: the rendered text the notifier composes leads with the
    # mention so Slack triggers the @channel ping.
    rendered = f"{prefix}URGENT! DEMO HOT: verify 2 receipt field(s) for x@y.com"
    assert rendered.startswith("<!channel> [DEMO·HOT]")


def test_hot_demo_mention_skipped_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty mention → tag-only prefix, no leading whitespace or token."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_MENTION", "")
    assign_to = {"managed": "awaitverify", "priority": "demo_hot"}
    assert _demo_prefix(assign_to) == "[DEMO·HOT] "


def test_standard_awaitverify_task_no_prefix() -> None:
    # A paying AwaitVerify task (priority=standard) is not a demo and
    # must not be tagged — billed customers shouldn't see [DEMO] in
    # their reviewer messages.
    assign_to = {"managed": "awaitverify", "priority": "standard"}
    assert _demo_prefix(assign_to) == ""


def test_high_priority_awaitverify_task_no_prefix() -> None:
    assign_to = {"managed": "awaitverify", "priority": "high"}
    assert _demo_prefix(assign_to) == ""


def test_regular_task_unchanged() -> None:
    # Non-AwaitVerify tasks (the open source path) must be untouched.
    assert _demo_prefix(None) == ""
    assert _demo_prefix({}) == ""
    assert _demo_prefix({"user_id": "U123"}) == ""


def test_other_managed_provider_no_prefix() -> None:
    # Defensive: a future `managed` value (e.g. another vertical wedge)
    # must not accidentally inherit the demo tag.
    assign_to = {"managed": "something-else", "priority": "demo"}
    assert _demo_prefix(assign_to) == ""


# ─── Hot-lane channel override ───────────────────────────────────────
#
# `_hot_lane_channel_override` picks the destination Slack channel id
# when a hot-lane (warm prospect) demo task fires. The contract:
#
#   - hot-lane demo + DEMO_HOT_SLACK_CHANNEL_ID set → return that id
#   - hot-lane demo + DEMO_HOT_SLACK_CHANNEL_ID unset → None (fallback)
#   - standard demo lane → None (default routing, never the hot channel)
#   - anything else (paying, OSS, unknown managed) → None
#
# The override short-circuits per-task `notify=["slack:#some-channel"]`
# routes for hot-lane only — that's intentional so the founder's pings
# land in one place regardless of what the caller put in `notify=`.


def test_hot_demo_uses_hot_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hot-lane demo + configured channel id → that id is returned."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", "C-HOT-LANE")
    assign_to = {"managed": "awaitverify", "priority": "demo_hot"}
    assert _hot_lane_channel_override(assign_to) == "C-HOT-LANE"


def test_hot_demo_falls_back_when_no_channel_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot-lane demo + no channel configured → None (no exception)."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", None)
    assign_to = {"managed": "awaitverify", "priority": "demo_hot"}
    assert _hot_lane_channel_override(assign_to) is None


def test_hot_demo_falls_back_when_channel_is_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty string is treated as unset — operator misconfiguration is
    silent fallback, never a hard error in the notifier path."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", "")
    assign_to = {"managed": "awaitverify", "priority": "demo_hot"}
    assert _hot_lane_channel_override(assign_to) is None


def test_demo_lane_does_not_use_hot_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public demo lane never lands in the founder channel even when
    DEMO_HOT_SLACK_CHANNEL_ID is configured."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", "C-HOT-LANE")
    assign_to = {"managed": "awaitverify", "priority": "demo"}
    assert _hot_lane_channel_override(assign_to) is None


def test_standard_priority_does_not_use_hot_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paying AwaitVerify tasks (priority=standard) keep default routing."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", "C-HOT-LANE")
    assign_to = {"managed": "awaitverify", "priority": "standard"}
    assert _hot_lane_channel_override(assign_to) is None


def test_oss_task_does_not_use_hot_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-AwaitVerify (OSS) tasks are never re-routed to the hot channel."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", "C-HOT-LANE")
    assert _hot_lane_channel_override(None) is None
    assert _hot_lane_channel_override({}) is None
    assert _hot_lane_channel_override({"user_id": "U123"}) is None


def test_other_managed_provider_does_not_use_hot_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future managed wedge using priority=demo_hot must not piggyback
    on AwaitVerify's hot channel."""
    monkeypatch.setattr(settings, "DEMO_HOT_SLACK_CHANNEL_ID", "C-HOT-LANE")
    assign_to = {"managed": "something-else", "priority": "demo_hot"}
    assert _hot_lane_channel_override(assign_to) is None
