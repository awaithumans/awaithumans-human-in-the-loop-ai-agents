# awaithumans v0.1.5 — PyPI rendering fix, tests CI, and a 224-keyword npm discovery push

Two days after [v0.1.4](https://github.com/awaithumans/awaithumans/releases/tag/v0.1.4). Maintenance + discovery release: a PyPI-rendering bug fix from beta-tester feedback, the GitHub Actions tests workflow that the repo had been missing, three follow-up cleanups CI surfaced on its first run, and a deliberate npm-keyword expansion. No API changes; safe drop-in upgrade.

## Highlights

- 🖼 **PyPI README images render again.** v0.1.4's README pointed at `raw.githubusercontent.com`, which serves `application/octet-stream` — PyPI's strict MIME check rejects non-image types and the demo GIF + logo silently didn't appear. Switched both to `cdn.jsdelivr.net/gh/`, which proxies GitHub content with the correct `image/gif` / `image/png` headers. npm rendered fine on 0.1.4; the fix is PyPI-specific. ([#124](https://github.com/awaithumans/awaithumans/pull/124))
- 🧪 **Tests CI.** New `.github/workflows/test.yml` runs three parallel jobs on every PR and every push to `main`: Python 3.11 + 3.13 matrix (pytest), TypeScript SDK (vitest + tsc), dashboard (vitest + tsc). Concurrency group cancels superseded runs. The existing `migrations.yml` continues to cover Alembic separately. ([#125](https://github.com/awaithumans/awaithumans/pull/125))
- 🐍 **Python 3.10 support is real again.** Two production modules + one test file imported `datetime.UTC`, which only exists in 3.11+. pyproject claimed 3.10 as the floor but the package crashed on import there. Swapped to the long-form `datetime.timezone.utc`. ([#126](https://github.com/awaithumans/awaithumans/pull/126))
- 🧹 **Temporal adapter test fixed.** `tests/adapters/test_temporal_adapter.py` still imported `_create_task_activity`, the pre-refactor name; the public symbol is now `awaithumans_create_task`. Updated four call sites. ([#127](https://github.com/awaithumans/awaithumans/pull/127))
- 🎨 **Ruff cleanup pass across the Python package.** 165 lint findings had accumulated on `main` (mostly import-sort + trailing-comma drift). `ruff check --fix` + `ruff format` handled 154; 14 manual edits cleaned up the rest. `ruff check .` is now green. ([#128](https://github.com/awaithumans/awaithumans/pull/128))
- 🔒 **TypeScript strictness up a notch.** Enabled `noUnusedLocals` + `noUnusedParameters` in `tsconfig.base.json`, then fixed the five real unused locals that surfaced (four in the dashboard, one duplicate `ZodType` import in the temporal adapter). Future regressions now fail `npm run typecheck` instead of slipping past the build. ([#129](https://github.com/awaithumans/awaithumans/pull/129))
- 🔍 **npm keyword expansion: 33 → 224.** A deliberate discovery experiment — npm is treated as a discovery surface rather than a one-liner index. The README, not the keyword list, judges fit. Pure metadata; no code change. ([#130](https://github.com/awaithumans/awaithumans/pull/130))
- 🤝 **Python and TypeScript stay mono-versioned at `0.1.5`** — TS SDK had been bumped solo for the keyword expansion; this release re-syncs Python alongside.

## Upgrade

### Python

```bash
pip install --upgrade "awaithumans[server]==0.1.5"
# or whichever extras you use:
#   pip install --upgrade "awaithumans[temporal]==0.1.5"
#   pip install --upgrade "awaithumans[langgraph]==0.1.5"
#   pip install --upgrade "awaithumans[verifier-claude]==0.1.5"
```

### TypeScript

```bash
npm install awaithumans@0.1.5
```

### Docker

```bash
docker pull ghcr.io/awaithumans/awaithumans:0.1.5
```

No migrations, no env var changes, no config changes.

## Compatibility

- **Python:** 3.10+ (`datetime.UTC` removal restores the declared floor).
- **Node:** 18+.
- **Database:** No new migrations.

## What's not in this release

- **mypy in CI** — pyproject opts in to `strict = true` but enforcing it in CI needs its own cleanup pass.
- **Re-enabling `ruff check .` in the tests workflow** — the lint findings are gone after #128; the CI step itself rolls into the next release.
- **Re-including the temporal adapter test** — the `--ignore=` flag in `test.yml` stays until the workflow update follows it.
