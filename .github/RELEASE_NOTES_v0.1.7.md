# awaithumans v0.1.7 — Docker image fixes for verifier + Slack first-click

One day after [v0.1.6](https://github.com/awaithumans/awaithumans/releases/tag/v0.1.6). Two fixes that both surfaced during a real first-time end-to-end demo. No API changes — **safe drop-in upgrade**.

## 🤖 Docker image ships all verifier extras

v0.1.6's image installed only the `[server]` extra. Any task sent with a `VerifierConfig(provider="claude" | "openai" | "gemini" | "azure_openai")` to the official image would fail on response submission with:

```
Verifier provider 'claude' requires the [verifier-claude] extra.
Install with: pip install "awaithumans[verifier-claude]"
```

…which an operator couldn't act on because the server lives inside the image. The `await_human()` call hung until timeout.

The image now ships all four verifier providers' extras (~35 MB added to the base). Works out of the box.

Closes [#142](https://github.com/awaithumans/awaithumans/issues/142). ([#143](https://github.com/awaithumans/awaithumans/pull/143))

## 🔗 Slack interactivity auto-links identity on first click

Previously, clicking `Open in Slack` / `Claim` on a task message hit:

> You're not in this server's user directory. Ask your operator to add you via Settings → Users.

…even when the clicker WAS the operator who installed the Slack app and signed up via `/setup`. Recovery required digging up two Slack IDs and pasting them into the dashboard's user-edit form.

Now: the server calls Slack's `users.info` API for the clicker's email and atomically binds the Slack identity to a matching directory user. Subsequent clicks hit the fast path. Zero operator-side configuration.

Requires the Slack app's bot token to have the `users:read.email` scope (in your Slack app config: OAuth & Permissions → Bot Token Scopes).

Closes [#144](https://github.com/awaithumans/awaithumans/issues/144). ([#145](https://github.com/awaithumans/awaithumans/pull/145))

## Upgrade

```bash
# Python
pip install --upgrade "awaithumans==0.1.7"
#   pip install --upgrade "awaithumans[server]==0.1.7"
#   pip install --upgrade "awaithumans[temporal]==0.1.7"
#   pip install --upgrade "awaithumans[langgraph]==0.1.7"
#   pip install --upgrade "awaithumans[verifier-claude]==0.1.7"
```

```bash
# TypeScript
npm install awaithumans@0.1.7
```

```bash
# Docker (now includes all verifier extras out of the box)
docker pull ghcr.io/awaithumans/awaithumans:0.1.7
# or
docker pull ghcr.io/awaithumans/awaithumans:latest
```

## Links

- 📚 [Documentation](https://docs.awaithumans.dev)
- 🆕 [What's new](https://docs.awaithumans.dev/changelog)
- 🔒 Security disclosures: **security@awaithumans.dev**
- 💬 [Discord](https://discord.gg/Kewdh7vjdc) · [GitHub Discussions](https://github.com/awaithumans/awaithumans/discussions)
- 🐛 [v0.1.6 → 0.1.7 full diff](https://github.com/awaithumans/awaithumans/compare/v0.1.6...v0.1.7)
