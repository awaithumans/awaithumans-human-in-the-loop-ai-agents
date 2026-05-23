# awaithumans v0.1.6 — README GIF fix + first-run UX polish

Four days after [v0.1.5](https://github.com/awaithumans/awaithumans/releases/tag/v0.1.5). Three small fixes, all surfaced by real first-run-onboarding debugging on a fresh machine. No API changes — **safe drop-in upgrade**.

## 🎬 PyPI README's demo GIF renders again

v0.1.5's hero GIF was 10.5 MB at 1402×720 / 33fps. PyPI's image proxy (Camo) rejects responses over ~5 MB, so the README on `pypi.org/project/awaithumans` displayed a broken-image icon for every visitor.

Re-encoded with ffmpeg's two-pass palette to **720px / 10fps / 64 colors → 3.24 MB** (69% smaller) while still showing the full demo flow.

## 🖋 First-run setup form has placeholder text on every field

Empty input boxes with just a small label gave new operators zero hint about the expected format. Watched a real user type an arbitrary placeholder-shaped string into the email field on `/setup`, succeed, then be unable to log in afterwards.

Placeholders added on both `/setup` and `/login` pages. Closes [#136](https://github.com/awaithumans/awaithumans/issues/136).

## ✅ Post-signup panel confirms which email you just registered

The "Operator created" screen previously jumped straight to SDK code examples without confirming what got persisted, so users had no way to recover the credentials they just typed.

Now shows a small brand-tinted "Signed in as" card with the email + display name above the code examples. Closes [#137](https://github.com/awaithumans/awaithumans/issues/137).

## Upgrade

```bash
# Python
pip install --upgrade "awaithumans==0.1.6"
#   pip install --upgrade "awaithumans[server]==0.1.6"
#   pip install --upgrade "awaithumans[temporal]==0.1.6"
#   pip install --upgrade "awaithumans[langgraph]==0.1.6"
#   pip install --upgrade "awaithumans[verifier-claude]==0.1.6"
```

```bash
# TypeScript
npm install awaithumans@0.1.6
```

```bash
# Docker
docker pull ghcr.io/awaithumans/awaithumans:0.1.6
# or
docker pull ghcr.io/awaithumans/awaithumans:latest
```

## Links

- 📚 [Documentation](https://docs.awaithumans.dev)
- 🆕 [What's new](https://docs.awaithumans.dev/changelog)
- 🔒 Security disclosures: **security@awaithumans.dev**
- 💬 [Discord](https://discord.gg/Kewdh7vjdc) · [GitHub Discussions](https://github.com/awaithumans/awaithumans/discussions)
- 🐛 [v0.1.5 → 0.1.6 full diff](https://github.com/awaithumans/awaithumans/compare/v0.1.5...v0.1.6)
