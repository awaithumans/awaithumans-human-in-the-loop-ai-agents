# Manual testing guide

A walk-through for exercising the awaithumans surface end-to-end —
both SDKs (Python + TypeScript), all three completion paths
(dashboard, email magic-link, Slack), against a real dev server. Use
this before cutting a release or after a refactor that touches the
wire format.

This is a manual checklist, not an automated test. Each scenario takes
about 30 seconds once the dev server is up.

## Prerequisites

- Python 3.10+ with `pip install "awaithumans[server]"`
- Node 20+ with `npm` (for the TypeScript scenarios)
- Slack workspace + a test Slack app (only for the Slack scenarios —
  see [`examples/slack-native/README.md`](examples/slack-native/README.md))

You don't need real SMTP / Resend / a verified domain to test email —
the `file` transport captures rendered emails to disk.

## 1. Start the dev server

In one terminal:

```sh
awaithumans dev
```

The first run generates two files:

- `<.awaithumans-dir>/payload.key` — used to encrypt at-rest columns
  and sign session cookies
- `<.awaithumans-dir>/admin.token` — the SDK reads this as a Bearer
  token

Note the path the CLI prints. In a second terminal:

```sh
export AWAITHUMANS_URL="http://localhost:3001"
export AWAITHUMANS_ADMIN_API_TOKEN="$(cat <.awaithumans-dir>/admin.token)"
```

Confirm the server is up:

```sh
curl -s "$AWAITHUMANS_URL/api/health"
# → {"status":"ok","version":"0.1.0"}
```

Open the dashboard at `http://localhost:3001`. First-run takes you
through a setup wizard; create yourself an operator account.

## 2. Python SDK — dashboard completion

Tests the smallest possible loop: create a task from Python, complete
it from the dashboard.

```sh
cd examples/quickstart
pip install -r requirements.txt
python refund.py
```

The script blocks. In the dashboard:

1. Open `http://localhost:3001` — see "Approve refund request" in the
   queue
2. Click the task, fill the form (Approved? Yes; Reason: "looks
   legitimate"), Submit Response
3. The Python script prints the typed response and exits

**Expected:** `✓ Refund approved. Reason: looks legitimate`

## 3. TypeScript SDK — dashboard completion

The same loop, driven from TypeScript.

```sh
cd examples/quickstart-ts
npm install
npm start
```

Same dashboard interaction as #2. Expected output identical.

## 4. Python SDK — email magic-link

Tests `await_human` with email notification, the file transport, and
the magic-link click flow. Fully automated — no human interaction
needed.

```sh
cd examples/email-smoke-py
pip install -r requirements.txt
python smoke.py
```

**Expected:** the script prints the captured-email path, the magic-
link URL it scraped, the 200 from POSTing it, and:

```
✓ smoke pass: Python SDK + email channel + magic-link round-trip
```

What this exercises:

- `await_human(notify=["email+<id>:..."])` wire path
- `extract_form` synthesizing a Switch primitive from the Pydantic
  response model
- The email channel's renderer emitting the magic-link buttons
- The signed token round-tripping through `/api/channels/email/action/`

## 5. TypeScript SDK — email magic-link

Same end-to-end loop as #4 but driven from the TS SDK. This is the
direct counterpart to the Python smoke and the test the cross-
language coverage hinges on.

```sh
cd examples/email-smoke
npm install
npm start
```

**Expected:** mirror of #4 output —

```
✓ smoke pass: TS SDK + email channel + magic-link round-trip
```

What this exercises that #4 doesn't:

- `awaitHuman`'s `apiKey` option (Python SDK uses the discovery
  file; TS SDK reads the env var)
- TS-side `extractForm` synthesizing the Switch (Python uses
  Pydantic's `extract_form`)
- Wire-format parity between the two SDKs

## 6. Email — dashboard fallback path

The magic-link path only triggers for a single-Switch / small-
SingleSelect response. For everything else the email renderer drops
in a "Review in dashboard" link-out instead. To hit this path:

1. Edit `examples/quickstart/refund.py` — change the response model
   to add a second field:

   ```python
   class RefundDecision(BaseModel):
       approved: bool = Field(...)
       note: str = Field(default="")  # ← second field disables magic-link
   ```

2. Add `notify=["email+<id>:test@example"]` to the `await_human_sync`
   call (after configuring an email identity per the smoke-test
   scripts above).

3. Run it. Inspect the captured email file (or your inbox if you
   pointed it at Resend) — there should be no Approve/Reject
   buttons, just a "Review in dashboard" link.

4. Click the dashboard link, complete the task. Script returns.

This is what gates which channel completion path users land in. The
single-field-Switch shortcut is opinionated; everything else is
expected to go through the dashboard.

## 7. Slack — DM completion

Tests the Slack DM flow end-to-end. Requires a Slack workspace and a
test app — see `examples/slack-native/README.md` for the one-time
setup (creates the OAuth app, configures the event + interactivity
URLs).

Once the Slack app is configured and the workspace is connected:

1. **Add yourself to the directory.** In the dashboard's Users page,
   click "Add user", paste your Slack handle (`@youhandle`), pick
   your workspace from the dropdown. Display name auto-fills.

2. **Run a slack-native example.** Pick the language you want to
   exercise — the flow is identical, only the SDK differs.

   Python:
   ```sh
   cd examples/slack-native
   python refund.py
   ```

   TypeScript:
   ```sh
   cd examples/slack-native-ts
   npm install
   npm start
   ```

   Either script creates a task with `notify=["slack:@youhandle"]`
   and blocks.

3. **Open the Slack DM the bot just sent you.** The message has an
   "Open in Dashboard" button (signed handoff URL — works even if
   you have no email/password) and an "Approve in Slack" button.

4. **Click "Approve in Slack".** A modal opens with the form.
   Fill it; Submit.

5. The Python script prints the response. The Slack message
   updates to "Completed by @youhandle" with no buttons (the
   recipient can't re-trigger).

**Expected:** task completes, audit log shows `completed_via_channel:
slack` and the right `completed_by_email` (or `@<slack_user_id>` for
Slack-only users).

## 8. Slack — broadcast claim

Same as #7 but with `notify=["slack:#<channel>"]`:

1. In `examples/slack-native/refund.py`, change `notify` to a
   channel: `notify=["slack:#approvals"]`
2. Run the script
3. The bot posts to `#approvals` with a "Claim this task" button
   (instead of "Approve in Slack")
4. Whoever clicks first becomes the assignee — the Slack message
   updates to "Claimed by @them", the modal opens for them
5. They submit, the message updates to "Completed by @them"

**Expected:** first claimer wins (test by clicking from two browsers
if you can — second click sees "This task is already claimed").

## 9. Cross-channel: email AND Slack on the same task

Tests that multiple `notify` entries fan out to both channels and
that whichever completes first wins.

1. Edit `examples/quickstart/refund.py` to add:
   ```python
   notify=["email+<id>:you@example.com", "slack:@youhandle"]
   ```
2. Run it
3. You receive both an email AND a Slack DM
4. Complete via either path
5. The OTHER channel's message updates to "Completed by ..." (the
   one you didn't use)

**Expected:** the un-used channel's interactive surface goes
non-interactive within ~1s of completion (the post-completion
updater for Slack runs as a background task).

## 10. Verifier — Claude rejection

Tests the AI-verifier rejection cycle.

1. Edit `examples/quickstart/refund.py`:
   ```python
   from awaithumans import claude_verifier
   ...
   verifier=claude_verifier(
       instructions="Reject if the reason is 'looks legitimate' or shorter than 30 chars.",
       max_attempts=2,
   )
   ```
2. Set `ANTHROPIC_API_KEY` in your shell
3. Run it
4. Complete with reason="looks legitimate" — the verifier rejects,
   the task goes back to non-terminal status, the dashboard re-
   prompts you
5. Complete again with a longer reason — passes

**Expected:** first submission shows the rejection reason inline;
second submission is accepted.

## 11. Cleanup

When you're done:

```sh
# Stop the dev server (Ctrl-C in the terminal running it)

# Delete any test email identities you created
curl -X DELETE \
  -H "Authorization: Bearer $AWAITHUMANS_ADMIN_API_TOKEN" \
  $AWAITHUMANS_URL/api/channels/email/identities/<smoke-id>

# The dev DB lives at <.awaithumans-dir>/dev.db — wipe to start over
rm -f <.awaithumans-dir>/dev.db
```

## What this guide doesn't cover

- **Temporal / LangGraph adapters.** Those have their own examples
  (`examples/temporal/`, `examples/langgraph/`) — run those if you've
  changed the adapter wire signal format.
- **Multi-workspace Slack.** The slack-native flow assumes one
  installation. Multi-tenant testing needs a second workspace + the
  `+identity` notify suffix.
- **Real Resend or SMTP.** The smoke tests use the `file` transport
  for determinism. If you've changed the Resend / SMTP transport
  paths, swap the identity's `transport` to `resend` and ship a real
  email to yourself.
- **Production CORS / TLS.** The dev server serves `*` CORS and HTTP
  by default. The validation tests in `tests/core/test_cors_validation.py`
  cover the prod paths.
