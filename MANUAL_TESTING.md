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

## AwaitVerify landing demo (smoke test before launch)

The landing demo at `/awaitverify` (inline teaser) and `/awaitverify/demo` (wizard) drives an authenticated reviewer task through the production AwaitVerify pipeline. Before pointing real visitors at it, run a hot-lane smoke yourself.

### One-time setup

Required env vars (set in your dev shell or `.env`):

```sh
# Cloudflare Turnstile (use the always-passes test secret for dev)
export AWAITHUMANS_TURNSTILE_SECRET=1x0000000000000000000000000000000AA

# Reviewer pool routing
export AWAITHUMANS_DEMO_REVIEWER_EMAIL=you@your-company.com
export AWAITHUMANS_DEMO_HOT_SLACK_CHANNEL_ID=C0123456789   # optional, hot-lane only
export AWAITHUMANS_DEMO_HOT_SLACK_MENTION='<!channel>'     # default; set empty to suppress

# Azure OpenAI deployment for extraction
export AWAITHUMANS_AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
export AWAITHUMANS_AZURE_OPENAI_DEPLOYMENT=gpt-5-vision    # your deployment name
export AWAITHUMANS_AZURE_OPENAI_API_KEY=<from-azure>
export AWAITHUMANS_AZURE_OPENAI_API_VERSION=2024-10-21

# Pool caps (defaults shown; tune to taste)
export AWAITHUMANS_DEMO_DAILY_CAP=50
export AWAITHUMANS_DEMO_PER_IP_CAP=3
export AWAITHUMANS_DEMO_PER_EMAIL_WINDOW_DAYS=7
export AWAITHUMANS_DEMO_DAILY_COST_CEILING_CENTS=500
export AWAITHUMANS_DEMO_CONFIDENCE_THRESHOLD=0.85
```

The reviewer's email must match a real operator user in the dashboard (so the per-field submit route can authorize against `task.assigned_to_email`).

### Walk-through

1. **Boot the server**

   ```sh
   awaithumans dev
   ```

   Confirm the demo routes are mounted:

   ```sh
   curl -s "$AWAITHUMANS_URL/api/openapi.json" | jq -r '.paths | keys[]' | grep demo
   # /api/demo/start
   # /api/demo/{demo_id}/status
   # /api/demo/{demo_id}/field/{field_name}/submit
   ```

2. **Boot the landing site**

   In the landing repo:

   ```sh
   cd awaithumans-landing
   cp .env.local.example .env.local   # then edit NEXT_PUBLIC_DEMO_API_URL
   npm install
   npm run dev
   ```

   Visit `http://localhost:3000/awaitverify`. Scroll to the "Try it" section.

3. **Public-lane demo**

   - Drop a 1-page invoice PDF (any will do) in the inline section.
   - Wizard opens at `/awaitverify/demo`.
   - Pick a preset (Invoice).
   - Enter your work email + complete Turnstile + click "Run extraction".
   - Wizard advances to the result page. High-confidence fields render immediately with a green check. Low-confidence fields show a pulsing "reviewer checking..." placeholder.

4. **Reviewer side**

   - In a separate browser session, sign in to the dashboard as the operator user matching `AWAITHUMANS_DEMO_REVIEWER_EMAIL`.
   - Find the new task. The title is prefixed `DEMO:` for public lane or `URGENT! DEMO HOT:` for hot lane.
   - For each pending field, hit the per-field submit endpoint directly while testing the live round trip:

     ```sh
     curl -X POST \
       -H "Cookie: <your dashboard session cookie>" \
       -H "Content-Type: application/json" \
       -d '{"value":"Acme Corp"}' \
       "$AWAITHUMANS_URL/api/demo/<demo_id>/field/vendor/submit"
     ```

     Once Task 34 (dashboard demo-mode UI) ships, this will be a button click in the dashboard. For now the per-field route is the contract.

5. **Watch the wizard update**

   - The result page polls every 2 seconds. Each field flashes green when it lands.
   - When the last pending field is submitted, the receipt email fires (check the file transport or your inbox if Resend is configured).

6. **Hot-lane demo**

   - Visit `/awaitverify?lane=hot` (or `/awaitverify/demo?lane=hot` directly). The wizard sets `is_hot_demo=true` on submit.
   - On the reviewer side, the Slack notification includes the configured mention (default `<!channel>`) and the task title is `URGENT! DEMO HOT: ...`.
   - Hot-lane wizard never times out the polling; public-lane wizard swaps to a "Reviewer's offline, we'll email it" message after 5 minutes if no claim.

7. **Reset**

   ```sh
   sqlite3 .awaithumans/dev.db 'DELETE FROM demo_records;'
   ```

### Things to verify before launch

- [ ] Free-email gate rejects `@gmail.com`, `@yahoo.com`, etc. (try one and confirm the inline error).
- [ ] Per-email weekly cap rejects a second submit from the same email within 7 days.
- [ ] Disposable-email gate rejects `@mailinator.com`.
- [ ] Receipt email lands in the visitor's inbox with the AI / corrections / final blocks rendered.
- [ ] Hot-lane Slack notification pings the configured channel and includes the mention.
- [ ] Multi-page PDF page picker actually renders thumbnails and submits the chosen page.
- [ ] Browser back/forward across wizard steps does not blank the state.
- [ ] Turnstile widget gates submission (try clicking submit before completing the challenge).

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
