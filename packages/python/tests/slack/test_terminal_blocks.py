"""Block Kit shape for `terminal_message_blocks`.

The notifier posts an interactive message with action buttons. When
the task transitions to a terminal state, we replace it via
chat.update with a non-interactive surface — these tests pin that
the replacement has no buttons (so the recipient can't re-trigger),
includes the right label/emoji per status, and keeps a "View in
dashboard" link only when we have one to offer.
"""

from __future__ import annotations

from awaithumans.server.channels.slack.blocks import terminal_message_blocks


def test_completed_renders_label_and_completer() -> None:
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="completed",
        completed_by_display="<@U_ALICE>",
        review_url="https://app.example/task?id=t1",
    )
    headline = blocks[0]["text"]["text"]
    assert "Completed" in headline
    assert "Approve KYC" in headline
    assert "<@U_ALICE>" in headline


def test_cancelled_uses_cancelled_label() -> None:
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="cancelled",
        completed_by_display=None,
        review_url=None,
    )
    headline = blocks[0]["text"]["text"]
    assert "Cancelled" in headline


def test_timed_out_omits_completer() -> None:
    """No human did the timing-out; we shouldn't claim someone did."""
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="timed_out",
        completed_by_display="<@U_BOB>",  # passed but should be ignored
        review_url=None,
    )
    headline = blocks[0]["text"]["text"]
    assert "Timed out" in headline
    assert "<@U_BOB>" not in headline


def test_review_url_renders_view_button() -> None:
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="completed",
        completed_by_display="<@U_ALICE>",
        review_url="https://app.example/task?id=t1",
    )
    # Section + actions with one button.
    assert len(blocks) == 2
    actions = blocks[1]
    assert actions["type"] == "actions"
    btns = actions["elements"]
    assert len(btns) == 1
    assert btns[0]["url"] == "https://app.example/task?id=t1"


def test_no_review_url_omits_actions_block() -> None:
    """Slack rejects an actions block with zero elements — when we
    don't have a URL, the block must not be emitted at all."""
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="cancelled",
        completed_by_display=None,
        review_url=None,
    )
    assert all(b.get("type") != "actions" for b in blocks)


def test_no_buttons_means_no_action_ids_anywhere() -> None:
    """Defense in depth: the recipient should be unable to dispatch
    any further interaction from the terminal message. Walk the
    block tree and assert there's no `action_id` other than the
    "View in dashboard" link (which is a URL, not an interaction)."""
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="completed",
        completed_by_display="<@U_ALICE>",
        review_url="https://app.example/task?id=t1",
    )
    for block in blocks:
        for element in block.get("elements", []):
            # If an element has an action_id but no URL, it's an
            # interaction button — those must not exist on the
            # terminal surface.
            if "action_id" in element and "url" not in element:
                raise AssertionError(f"Terminal blocks contain interactive element: {element}")


def test_unknown_status_falls_back_to_closed() -> None:
    """Status enum may grow; the renderer must not crash on an
    unfamiliar value (would block the chat.update path)."""
    blocks = terminal_message_blocks(
        task_title="Approve KYC",
        status="some_future_status",
        completed_by_display=None,
        review_url=None,
    )
    assert "Closed" in blocks[0]["text"]["text"]
