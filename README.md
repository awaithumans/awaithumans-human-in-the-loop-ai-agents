# awaithumans — HITL infrastructure for AI agents

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/logo/dark.svg">
  <img alt="awaithumans" src="https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/logo/light.svg" width="520">
</picture>

<br>

**Your agents already await promises. Now they can await humans.**

<br>

[![PyPI installs](https://img.shields.io/pepy/dt/awaithumans?style=for-the-badge&label=PyPI%20installs&color=3775A9&logo=pypi&logoColor=white)](https://pepy.tech/project/awaithumans)
[![npm installs](https://img.shields.io/npm/dt/awaithumans?style=for-the-badge&label=npm%20installs&color=CB3837&logo=npm&logoColor=white)](https://www.npmjs.com/package/awaithumans)
[![GitHub stars](https://img.shields.io/github/stars/awaithumans/awaithumans?style=for-the-badge&color=FBBF24&logo=github&logoColor=white&label=GitHub%20stars)](https://github.com/awaithumans/awaithumans)

[![PyPI](https://img.shields.io/pypi/v/awaithumans?label=pypi&color=3775A9&logo=pypi&logoColor=white)](https://pypi.org/project/awaithumans/)
[![npm](https://img.shields.io/npm/v/awaithumans?label=npm&color=CB3837&logo=npm&logoColor=white)](https://www.npmjs.com/package/awaithumans)
[![CI](https://img.shields.io/github/actions/workflow/status/awaithumans/awaithumans/test.yml?label=tests&logo=github&logoColor=white)](https://github.com/awaithumans/awaithumans/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](https://github.com/awaithumans/awaithumans/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Discord](https://img.shields.io/badge/discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/Kewdh7vjdc)

[**Docs**](https://docs.awaithumans.dev) · [**Quickstart**](https://docs.awaithumans.dev/quickstart) · [**Examples**](./examples) · [**Discord**](https://discord.gg/Kewdh7vjdc)

</div>

<br>

**HITL infrastructure for AI agents — open source.** A single primitive (`await_human()` / `awaitHuman()`) your agent calls when it needs a human. A real person reviews via Slack / email / a built-in dashboard, submits a typed response, and your agent resumes — like awaiting any other promise.

```python
from awaithumans import await_human
from pydantic import BaseModel

class Decision(BaseModel):
    approved: bool
    note: str | None = None

decision = await await_human(
    task="Approve refund request",
    payload_schema=RefundRequest,
    payload=RefundRequest(order_id="A-4721", amount_usd=180),
    response_schema=Decision,
    timeout_seconds=900,
)

if decision.approved:
    process_refund(...)
```

The agent waits on `decision` like it waits on any other Promise or Future.
A human gets notified ([Slack](https://slack.com), email, dashboard), reviews the request, and
submits a typed response. The agent resumes with the typed answer.

![awaithumans demo — an agent creates a task, a human reviews it, the agent resumes with the typed response](https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/images/hero-demo.gif)

![The awaithumans dashboard — pending tasks queued for human review](https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/images/hero-dashboard.png)

---

## The problem

Every production agent hits a wall where the model can't or shouldn't
proceed alone. Three distinct walls, each permanent:

- **Judgment.** The agent has the information but can't be trusted to
  decide. High liability, regulation, or consequence — KYC approvals,
  refund sign-offs, content moderation escalations. A human carries the
  accountability.
- **System-uncertainty.** The agent doesn't know the state of the world.
  The source of truth is in a bank dashboard, a partner system, a
  manual file. No model can close this gap. A human investigates and
  tells the agent what's true.
- **Embodiment.** The task requires a real person — signing, calling,
  picking something up, passing a CAPTCHA. Not a model problem.

Better models don't solve walls 2 and 3. `awaithumans` makes the call
to a human a first-class primitive instead of a pile of bespoke glue
per project.

---

## Why awaithumans

|  | **awaithumans** | humanlayer | DIY glue code |
|---|---|---|---|
| **Maintained** | ✅ Active development | ❌ Abandoned | — |
| **Setup time** | One command (`awaithumans dev`) | Per-customer rebuild | Weeks |
| **Channels** | Slack + email + built-in dashboard | Slack only | Build each yourself |
| **Typed responses** | ✅ [Pydantic](https://pydantic.dev) (Python) / [Zod](https://zod.dev) (TS) — schema-validated end to end | Partial | Build each yourself |
| **Restart-safe** | ✅ Stripe-style idempotency — agent resumes across restarts | ❌ | Build each yourself |
| **AI pre-verification** | ✅ [Claude](https://www.anthropic.com/claude) / [OpenAI](https://openai.com) / [Gemini](https://gemini.google.com) / [Azure](https://azure.microsoft.com/en-us/products/ai-services/openai-service) — pre-check the human's answer before the agent trusts it | ❌ | Build each yourself |
| **Workflow engines** | ✅ [Temporal](https://temporal.io) + [LangGraph](https://langchain-ai.github.io/langgraph/) adapters — hand the wait to the engine | ❌ | Build each yourself |
| **Self-hostable** | ✅ Docker + Postgres in one command | SaaS-only | — |
| **License** | Apache 2.0 (patent grant) | — | — |

Built by engineers who hit the HITL wall three times in production fintech and ScaleBrick agent systems — and watched the only OSS alternative get abandoned by its founder over per-customer-fork creep. The architecture has exactly four extension points (channels, verifiers, routers, task-type handlers) so no single customer can push the core into the same trap.

---

## Quick start

**60 seconds. Two terminals.** First-time setup needs a browser click; everything else is paste-and-run.

**Terminal 1** — server + dashboard:

```bash
pip install "awaithumans[server]" && awaithumans dev
```

Click the setup URL it prints, create your operator account. The dashboard is now at `http://localhost:3001`.

**Terminal 2** — paste this whole block:

```bash
pip install awaithumans pydantic && cat > /tmp/refund.py <<'PY'
from awaithumans import await_human_sync
from pydantic import BaseModel

class RefundRequest(BaseModel):
    order_id: str
    amount_usd: float

class Decision(BaseModel):
    approved: bool

d = await_human_sync(
    task="Approve refund of $180?",
    payload_schema=RefundRequest,
    payload=RefundRequest(order_id="A-4721", amount_usd=180),
    response_schema=Decision,
    timeout_seconds=300,
)
print("approved" if d.approved else "rejected")
PY
python /tmp/refund.py
```

The script blocks. Open the dashboard, click the pending task, hit **Approve**. The script unblocks with the typed `Decision`.

That's the full loop. From here, swap the schema for your own, add `notify=["slack:#ops", "email:ops@yourco.com"]` to route the task elsewhere, or wrap the call in a Temporal / LangGraph workflow.

More examples — refund, KYC, content moderation, Slack-native, Temporal, LangGraph — in [`examples/`](./examples/).

---

## What you can build with it

Real production patterns this primitive collapses into a single function call:

- **High-value approvals** — refunds above a threshold, wire transfers, plan upgrades, contract renewals. Agent prepares the case (Pydantic payload), human signs off with a typed decision (approved + reason).
- **KYC / identity review** — agent flags borderline documents, human inspects, sends back `verified: bool` with notes. Pair with `verifier=verify_with_claude(...)` to pre-check the reviewer's reasoning.
- **Content moderation escalation** — AI tags a borderline post; instead of hard-deciding, it calls `await_human()` with the content + AI's reasoning + a Switch for keep/remove. Reviewer's decision flows back into the moderation pipeline.
- **Agent-generated code review** — your LLM drafts a pull request; before merge, the agent waits for a human to approve via Slack. The "Claim this task" button assigns it to whoever's on rotation.
- **Customer-success escalation** — support agent answers FAQs; on a complex thread, it escalates to a human with the full transcript as the payload. Human writes the reply, agent posts it.
- **Scrape-and-CAPTCHA fallback** — automation hits a CAPTCHA wall, calls `await_human()` with the screenshot, a human solves it, agent resumes the scrape.

Anything where an LLM's confidence is too low, the liability too high, or the source of truth lives outside the model's reach — it's HITL-shaped, and this primitive fits.

---

## Complete tasks in Slack

Add `notify=["slack:#ops"]` and the task lands in the channel with a
"Claim this task" button. First clicker atomically wins; their
response form opens as a modal. Completing it unblocks the agent
just like the dashboard path.

![Slack broadcast — a task posted to a channel with a Claim button](https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/images/slack-broadcast.png)

```python
decision = await await_human(
    task="Approve refund",
    payload_schema=RefundRequest,
    payload=RefundRequest(order_id="A-4721", amount_usd=250),
    response_schema=Decision,
    timeout_seconds=900,
    notify=["slack:#ops"],
)
```

Works for direct messages too (`slack:@U01ABC234`) and email
(`email:reviewer@company.com`).

---

## Durable mode

When your agent runs inside a workflow engine, you don't want a
long-poll hanging on the orchestrator's thread for 15 minutes. The
[Temporal](https://temporal.io) and [LangGraph](https://langchain-ai.github.io/langgraph/) adapters hand the wait to the engine itself:

**Temporal** — signal-based suspend + callback:

```python
from awaithumans.adapters.temporal import await_human_temporal

decision = await await_human_temporal(
    task="Approve refund",
    payload_schema=RefundRequest,
    payload=...,
    response_schema=Decision,
    timeout_seconds=900,
)
```

**LangGraph** — interrupt/resume:

```python
from awaithumans.adapters.langgraph import await_human_langgraph
```

Same `await_human` shape, same typed response. The adapter just
changes how the wait is orchestrated.

---

## AI verification

Ask an AI to pre-check the human's work before the agent trusts it.
Catches the "human clicked approve without reading" case:

```python
from awaithumans.verifiers.claude import verify_with_claude

decision = await await_human(
    task="Approve refund",
    payload_schema=RefundRequest,
    payload=...,
    response_schema=Decision,
    verifier=verify_with_claude(
        instructions="Reject if the note contradicts the approval.",
        max_attempts=2,
    ),
)
```

The verifier runs after each human submission. If it fails, the task
is re-sent to the human with the verifier's reason attached.

---

## TypeScript

```bash
npm install awaithumans
```

```ts
import { awaitHuman } from "awaithumans";
import { z } from "zod";

const RefundRequest = z.object({
  orderId: z.string(),
  amountUsd: z.number(),
});

const Decision = z.object({
  approved: z.boolean(),
});

const decision = await awaitHuman({
  task: "Approve refund",
  payloadSchema: RefundRequest,
  payload: { orderId: "A-4721", amountUsd: 180 },
  responseSchema: Decision,
  timeoutMs: 900_000,
});
```

Full walkthrough: [`examples/quickstart-ts/`](./examples/quickstart-ts/).

The server + dashboard are Python — TypeScript runs `npx awaithumans
dev` (via [uv](https://astral.sh/uv)) so you never touch a Python env.

---

## Self-hosted

```bash
docker run -p 3001:3001 ghcr.io/awaithumans/awaithumans:latest
```

Or `docker compose up` with the included `docker-compose.yml`
(optional Postgres block inside). Backs everything — API, dashboard,
channels — from one image.

---

## Architecture

- **Core primitive:** one function, `await_human()` / `awaitHuman()`,
  typed-in-typed-out.
- **Task store:** SQLite in dev, Postgres in prod. Idempotency keys,
  atomic state transitions, audit trail.
- **Channels:** Slack + email today. Plug in your own by implementing
  a small interface (`server/channels/`).
- **Verifiers:** [Claude](https://www.anthropic.com/claude) today, [OpenAI](https://openai.com), [Gemini](https://gemini.google.com), and [Azure OpenAI](https://azure.microsoft.com/en-us/products/ai-services/openai-service) shipped; any provider is a one-file adapter.
- **Router:** least-recently-assigned over a user directory with
  free-form `role`/`access_level`/`pool` labels.
- **Task-type handlers:** forms auto-generated from your [Pydantic](https://pydantic.dev) /
  [Zod](https://zod.dev) schema, rendered per channel (Slack Block Kit, email, web form).

Every customization flows through one of these four buckets. That's
the entire extension surface.

---

## Documentation

- **Quickstart:** [`examples/quickstart/`](./examples/quickstart/)
- **Full docs:** [docs.awaithumans.dev](https://docs.awaithumans.dev)
- **Contributing:** [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- **Security policy:** [`SECURITY.md`](./SECURITY.md)

---

## Built with awaithumans?

Drop this badge in your project's README so folks know where the HITL primitive came from:

[![Powered by awaithumans](https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/images/badges/powered-by-awaithumans.svg)](https://github.com/awaithumans/awaithumans)

```markdown
[![Powered by awaithumans](https://raw.githubusercontent.com/awaithumans/awaithumans/main/docs/images/badges/powered-by-awaithumans.svg)](https://github.com/awaithumans/awaithumans)
```

Or generate a generic variant via [shields.io](https://shields.io):

```markdown
[![Powered by awaithumans](https://img.shields.io/badge/Powered_by-awaithumans-00E676)](https://github.com/awaithumans/awaithumans)
```

---

## Troubleshooting

If something isn't working, run the pre-flight check first:

```bash
awaithumans doctor
```

It scans your environment for the most common misconfigurations —
missing or malformed `PAYLOAD_KEY`, Slack token issues, unreachable
database, Docker bind-mount gotchas — and prints a clear report with
actionable fixes. Catches ~80% of first-run issues in under 2 seconds.

---

## Packages

| Package | Registry | License |
|---|---|---|
| `awaithumans` (Python SDK + server + CLI + dashboard) | PyPI | Apache 2.0 |
| `awaithumans` (TypeScript SDK) | npm | Apache 2.0 |
| `ghcr.io/awaithumans/awaithumans` (container) | GHCR | Apache 2.0 |

**License:** [Apache License 2.0](LICENSE). Permissive, OSI-approved,
with an explicit patent grant. Use it in proprietary stacks, fork it,
ship it inside paid products — no fee, no contact required. The only
thing the license asks is that you preserve the notice and don't use
the project's trademarks without permission.

---

## Status

**v0.1.0 — public preview.** Released 2026-05-11.

The full primitive, all three channels (dashboard / Slack / email), both durable adapters (Temporal / LangGraph), AI verification across four providers, and one-command self-hosting are all live in this release.

This is a young project — APIs are stable for v0.x, but expect rough edges in the long tail. File issues, open PRs, drop questions in [Discussions](https://github.com/awaithumans/awaithumans/discussions) or [Discord](https://discord.gg/Kewdh7vjdc). Every reproducible bug report shipped with a fix in v0.2.

For the post-launch roadmap — local task book for runtimes without an orchestrator, custom router strategies, post-launch hardening — see [Roadmap & help wanted](https://docs.awaithumans.dev/community/roadmap).

---

## Contributors

awaithumans is built by the maintainers and these contributors:

- [@HirenGajjar](https://github.com/HirenGajjar) — `awaithumans doctor` pre-flight checks ([#150](https://github.com/awaithumans/awaithumans/pull/150))

Want to be on this list? Pick a [`good first issue`](https://github.com/awaithumans/awaithumans/labels/good%20first%20issue), drop a comment on it to claim, and we'll review your PR within ~24h.
