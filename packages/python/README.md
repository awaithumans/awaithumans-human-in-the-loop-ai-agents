# awaithumans — HITL infrastructure for AI agents

<div align="center">

<img alt="awaithumans" src="https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/logo/light.svg" width="520">

<br>

**Your agents already await promises. Now they can await humans.**

<br>

[![PyPI installs](https://img.shields.io/pepy/dt/awaithumans?style=for-the-badge&label=PyPI%20installs&color=3775A9&logo=pypi&logoColor=white)](https://pepy.tech/project/awaithumans)
[![GitHub stars](https://img.shields.io/github/stars/awaithumans/awaithumans-human-in-the-loop-ai-agents?style=for-the-badge&color=FBBF24&logo=github&logoColor=white&label=GitHub%20stars)](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents)

[![PyPI](https://img.shields.io/pypi/v/awaithumans?label=pypi&color=3775A9&logo=pypi&logoColor=white)](https://pypi.org/project/awaithumans/)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/blob/main/LICENSE)
[![Discord](https://img.shields.io/badge/discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/Kewdh7vjdc)

[**Docs**](https://docs.awaithumans.dev) · [**Quickstart**](https://docs.awaithumans.dev/quickstart) · [**Examples**](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/tree/main/examples) · [**Discord**](https://discord.gg/Kewdh7vjdc)

</div>

<br>

**HITL infrastructure for AI agents — open source.** A single primitive (`await_human()`) your agent calls when the model can't or shouldn't proceed alone. A human gets notified ([Slack](https://slack.com), email, or dashboard), reviews the request, submits a typed response, and your agent resumes — like awaiting any other coroutine.

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

![awaithumans demo — an agent creates a task, a human reviews it, the agent resumes with the typed response](https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/images/hero-demo.gif)

![The awaithumans dashboard — pending tasks queued for human review](https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/images/hero-dashboard.png)

---

## Install

```bash
pip install awaithumans                    # SDK only — lightweight HTTP client
pip install "awaithumans[server]"          # SDK + server + CLI + bundled dashboard
```

Extras for specific adapters:

```bash
pip install "awaithumans[temporal]"        # Temporal workflow adapter
pip install "awaithumans[langgraph]"       # LangGraph interrupt/resume adapter
pip install "awaithumans[verifier-claude]" # AI verification via Claude
```

Extras stack — install multiple in one command:

```bash
pip install "awaithumans[server,temporal,verifier-claude]"
```

---

## Quick start

```bash
pip install "awaithumans[server]"
awaithumans dev
```

First run prints a setup URL. Open it, create the operator account,
you're in. The dashboard runs on `http://localhost:3001`.

Then your agent:

```python
from awaithumans import await_human_sync
from pydantic import BaseModel

class RefundRequest(BaseModel):
    order_id: str
    amount_usd: float

class Decision(BaseModel):
    approved: bool

decision = await_human_sync(
    task="Approve refund",
    payload_schema=RefundRequest,
    payload=RefundRequest(order_id="A-4721", amount_usd=180),
    response_schema=Decision,
    timeout_seconds=900,
)
```

`await_human_sync` is the blocking form. For async agents use
`await_human` directly.

---

## Routing

Route tasks to people (not channels) via `assign_to`:

```python
decision = await await_human(
    task="...",
    assign_to={"role": "kyc-reviewer", "access_level": "senior"},
    ...,
)
```

The server picks the least-recently-assigned active user matching the
filter — fair distribution across your team. Manage the user directory
via the dashboard's Settings page or the CLI:

```bash
awaithumans add-user --email alice@company.com --role kyc-reviewer --access-level senior
awaithumans list-users
awaithumans remove-user alice@company.com
awaithumans set-password alice@company.com
```

---

## Notifications

```python
notify=["slack:#ops", "email:reviewer@company.com"]
```

Slack channel broadcasts post a "Claim this task" button; first
clicker atomically wins. Direct messages and emails go straight to
the recipient.

![Slack broadcast — a task posted to a channel with a Claim button](https://cdn.jsdelivr.net/gh/awaithumans/awaithumans-human-in-the-loop-ai-agents@main/docs/images/slack-broadcast.png)

---

## Durable workflows

```python
from awaithumans.adapters.temporal import await_human_temporal
from awaithumans.adapters.langgraph import await_human_langgraph
```

Same `await_human` shape. The adapter hands the wait to the engine
([Temporal](https://temporal.io) signal / [LangGraph](https://langchain-ai.github.io/langgraph/) interrupt) so the orchestrator isn't
holding a connection open for 15 minutes.

---

## AI verification

```python
from awaithumans.verifiers.claude import verify_with_claude

decision = await await_human(
    task="...",
    verifier=verify_with_claude(
        instructions="Reject if the note contradicts the approval.",
        max_attempts=2,
    ),
)
```

The verifier runs after each human submission; failures re-send to the
human with the reason attached. [Claude](https://www.anthropic.com/claude), [OpenAI](https://openai.com), [Gemini](https://gemini.google.com), and [Azure OpenAI](https://azure.microsoft.com/en-us/products/ai-services/openai-service) are all supported.

---

## Documentation

- **Repository:** [github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents)
- **Full docs:** [awaithumans.dev](https://awaithumans.dev)
- **Examples:** [`examples/quickstart/`](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/tree/main/examples/quickstart) and [`examples/quickstart-ts/`](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/tree/main/examples/quickstart-ts)
- **Changelog:** [`CHANGELOG.md`](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/blob/main/CHANGELOG.md)

---

## License

[Apache License 2.0](https://github.com/awaithumans/awaithumans-human-in-the-loop-ai-agents/blob/main/LICENSE)
across the whole package — SDK, server, dashboard, adapters, channels.
Permissive, OSI-approved, with an explicit patent grant. Use it in
proprietary stacks, fork it, ship it inside paid products.
