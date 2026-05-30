# Quickstart (TypeScript)

The smallest thing that proves `awaithumans` works end-to-end from a
TypeScript agent: the agent asks a human to approve a refund, the
human clicks a button, the agent gets the typed response back.

Mirrors [`../quickstart/`](../quickstart/) (Python), using the same
server.

## Prerequisites

- Node 20+
- [uv](https://docs.astral.sh/uv/) — a tiny Python runtime manager.
  The `npx awaithumans` wrapper uses it to run the Python server
  without forcing you to touch a Python env.

Install uv (one line):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Run

**Terminal 1** — the server + dashboard:

```bash
npx awaithumans dev
```

You should see:

```
Ready — waiting for tasks...
Dashboard at http://0.0.0.0:3001
```

**Terminal 2** — the example agent:

```bash
npm install
npm start
```

You should see:

```
→ creating task on the awaithumans server...
  Open http://localhost:3001 to review.
```

## What to do

1. Open <http://localhost:3001>. The task appears in the queue:
   **“Approve refund request”**.
2. Click the row. The detail view shows the request (order, customer,
   amount, reason) and a form with two fields — **Approve?** (toggle)
   and **Note to customer** (textarea).
3. Fill it in. Click **Submit response**.

Terminal 2 unblocks and prints:

```
✓ Refund approved. Note: Issuing refund immediately.
```

## What the code looks like

```ts
import { z } from "zod";
import { awaitHuman } from "awaithumans";

const RefundRequest = z.object({
  orderId: z.string(),
  customer: z.string(),
  amountUsd: z.number(),
  reason: z.string(),
});

const Decision = z.object({
  approved: z.boolean(),
  note: z.string().optional(),
});

const decision = await awaitHuman({
  task: "Approve refund request",
  payloadSchema: RefundRequest,
  payload: {
    orderId: "A-4721",
    customer: "jane@example.com",
    amountUsd: 180,
    reason: "Item arrived damaged",
  },
  responseSchema: Decision,
  timeoutMs: 900_000,
});

if (decision.approved) {
  // ...
}
```

Zod schemas on both sides: `payloadSchema` drives what the human sees,
`responseSchema` drives the form they fill out, and your agent gets the
typed `decision` back. No JSON-twiddling.

## Next steps

- **Temporal:** `import { awaitHuman } from "awaithumans/temporal"` for
  signal-based durable workflows.
- **LangGraph:** `import { awaitHuman } from "awaithumans/langgraph"`
  for interrupt/resume in a LangGraph agent.
- **Send to Slack or email:** add `notify: ["slack:#ops"]` or
  `notify: ["email:reviewer@company.com"]`. Needs the channel
  configured on the server — see [docs](https://docs.awaithumans.dev).
- **Docker:** don't want to install uv? `docker run -p 3001:3001
  ghcr.io/awaithumans/awaithumans-human-in-the-loop-ai-agents:latest` runs the same server.
- **Python:** the same flow in Python —
  [`examples/quickstart/`](../quickstart/).
