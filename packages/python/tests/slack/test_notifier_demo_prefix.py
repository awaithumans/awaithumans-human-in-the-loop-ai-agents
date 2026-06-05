"""AwaitVerify demo tasks get a visible tag in the Slack message text.

Reviewers run a shared queue — without the tag, free demo tasks look
identical to paying ones. The hot-lane variant (warm prospects) gets a
distinct middle-dot tag so the founder can spot pings instantly.

This test exercises the pure helper directly; the integration with the
post-message flow is verified by the broader notifier e2e tests.
"""

from __future__ import annotations

from awaithumans.server.channels.slack.notifier import _demo_prefix


def test_demo_task_gets_demo_prefix() -> None:
    assign_to = {"managed": "awaitverify", "priority": "demo"}
    assert _demo_prefix(assign_to) == "[DEMO] "


def test_demo_hot_task_gets_demo_hot_prefix() -> None:
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
