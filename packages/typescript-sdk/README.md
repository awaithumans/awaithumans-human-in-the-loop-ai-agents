# awaithumans — HITL infrastructure for AI agents

<div align="center">

<img alt="awaithumans" src="https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/logo/light.svg" width="520">

<br>

**Your agents already await promises. Now they can await humans.**

<br>

[![npm installs](https://img.shields.io/npm/dt/awaithumans?style=for-the-badge&label=npm%20installs&color=CB3837&logo=npm&logoColor=white)](https://www.npmjs.com/package/awaithumans)
[![GitHub stars](https://img.shields.io/github/stars/awaithumans/awaithumans-human-in-the-loop-ai-agents?style=for-the-badge&color=FBBF24&logo=github&logoColor=white&label=GitHub%20stars)](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents)

[![npm](https://img.shields.io/npm/v/awaithumans?label=npm&color=CB3837&logo=npm&logoColor=white)](https://www.npmjs.com/package/awaithumans)
[![Node](https://img.shields.io/badge/node-20%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/blob/main/LICENSE)
[![Discord](https://img.shields.io/badge/discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/Kewdh7vjdc)

[**Docs**](https://docs.awaithumans.dev) · [**Quickstart**](https://docs.awaithumans.dev/quickstart) · [**Examples**](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/tree/main/examples/quickstart-ts) · [**Discord**](https://discord.gg/Kewdh7vjdc)

</div>

<br>

**HITL infrastructure for AI agents — open source.** A single primitive (`awaitHuman()`) your agent calls when the model can't or shouldn't proceed alone. A human gets notified ([Slack](https://slack.com), email, or dashboard), reviews the request, submits a typed response, and your agent resumes — like awaiting any other promise.

```ts
import { awaitHuman } from "awaithumans";
import { z } from "zod";

const RefundRequest = z.object({
  orderId: z.string(),
  amountUsd: z.number(),
});

const Decision = z.object({
  approved: z.boolean(),
  note: z.string().optional(),
});

const decision = await awaitHuman({
  task: "Approve refund request",
  payloadSchema: RefundRequest,
  payload: { orderId: "A-4721", amountUsd: 180 },
  responseSchema: Decision,
  timeoutMs: 900_000,
});

if (decision.approved) {
  await processRefund(...);
}
```

![awaithumans demo — an agent creates a task, a human reviews it, the agent resumes with the typed response](https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/images/hero-demo.gif)

![The awaithumans dashboard — pending tasks queued for human review](https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/images/hero-dashboard.png)

---

## Install

```bash
npm install awaithumans
# or
pnpm add awaithumans
# or
bun add awaithumans
```

Works in Node 20+, Bun, Deno, and edge runtimes (Cloudflare Workers,
Vercel Edge). No `node:*` imports.

---

## Run the server

The awaithumans server (which handles task storage, Slack/email
channels, and hosts the review dashboard) is written in Python. As a
TypeScript developer you don't have to touch a Python environment —
the npm CLI wraps it:

```bash
npx awaithumans dev
```

Under the hood this uses [uv](https://astral.sh/uv) to fetch + run the
Python server on demand. Install uv once:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # unix
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # windows
```

First run prints a setup URL. Open it, create the operator account,
you're in. The dashboard is at `http://localhost:3001`.

Prefer Docker? `docker run -p 3001:3001 ghcr.io/awaithumans/awaithumans:latest`.

Tasks can be delivered to Slack channels with a "Claim this task" button — first clicker atomically wins, response form opens as a modal, agent unblocks when they submit. Add `notify: ["slack:#ops"]` to the `awaitHuman()` call:

![Slack broadcast — a task posted to a channel with a Claim button](https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/images/slack-broadcast.png)

---

## Durable workflows

When your agent runs inside [Temporal](https://temporal.io) or [LangGraph](https://langchain-ai.github.io/langgraph/), you don't want the
wait sitting on an orchestrator thread for 15 minutes:

```ts
// Temporal — signal-based suspend + callback
import { awaitHuman } from "awaithumans/temporal";

// LangGraph — interrupt/resume
import { awaitHuman } from "awaithumans/langgraph";
```

Same `awaitHuman` shape, same typed response. The adapter just
changes how the wait is orchestrated.

---

## Testing

An in-memory mock client so your agent tests don't need a running
server:

```ts
import { createTestClient } from "awaithumans/testing";

const client = createTestClient();

// Drive the human's response programmatically.
client.onAwait((task) => ({ approved: true, note: "looks good" }));
```

---

## Documentation

- **Repository:** [github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents)
- **Full docs:** [awaithumans.dev](https://awaithumans.dev)
- **Quickstart:** [`examples/quickstart-ts/`](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/tree/main/examples/quickstart-ts)
- **Changelog:** [`CHANGELOG.md`](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/blob/main/CHANGELOG.md)

---

## License

[Apache License 2.0](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/blob/main/LICENSE).
The TypeScript SDK, every adapter subpath export, and the Python
server + dashboard it talks to are all under the same license —
permissive, OSI-approved, with an explicit patent grant.
