# 0007. Fork kit: quick to run, easy to extend, safe to publish

Status: accepted (owner-delegated roadmap step 4, 2026-11-27). Builds on ADR 0001, 0004, 0005 and 0006.

## Context
Sebastian is meant to be forked by one person who runs it for themselves. A fork is only useful if it runs within minutes without accounts, if the ways to extend it are written down in one place, and if nothing private from the original owner ships with it.

## Decision
- **Quick run.** The README opens with a three-command offline path: clone, install (`uv sync` or `pip install -e .`), `sebastian init` then `sebastian doctor` then `sebastian chat`. It needs no account, key or network. A reply language is chosen at `init` (`--language en`).
- **Easy integration.** `CONTRIBUTING.md` is the single entry point and points to the existing guides instead of repeating them: a channel (ADR 0002, `channels/base.py`, the setup guides), a language (`docs/LANGUAGES.md`), a plugin (ADR 0005, `plugins.py`). New features arrive as a registry entry with their own switch, default off until approved.
- **Process.** A change that alters behaviour, a boundary or a default starts with an ADR in `docs/adr/`; the roadmap names the step. Small fixes need no ADR.
- **Two rules that never bend.** Every write (calendar, tasks that need a preview, messages) is shown as a preview and needs an explicit confirmation. Tests never touch the live network or a real account; transports are faked and the suite runs offline.
- **Trust model written down.** `SECURITY.md` states what Sebastian protects (one owner, local data, secrets outside the repo, nothing sent to a provider without consent), where secrets live, and how to report a problem privately.
- **CI without secrets.** `.github/workflows/ci.yml` runs `uv sync --extra dev` and `pytest` on push and pull request, offline, with read-only permissions and no secrets. Issue and pull-request templates repeat the two rules above.
- **No private data in the tree.** Examples, tests and docs use fictional names, ids and domains (`example.com`, `me@corp.example`, `1000000001`). References to the original owner's private projects were removed from the code, config example and docs. The legacy design notes in `docs/legacy/` keep the former assistant name but no personal data.

## Consequences
- Contributors have one place to start and a short checklist; reviewers can point to the rules.
- The offline suite doubles as the CI gate and as the proof that a fresh clone works.
- Git history still carries the original author's commit identity; scrubbing it would rewrite published history and is left to the owner.
- Not built: a release process, packaging to PyPI, a code of conduct beyond the short contributor note, GitHub repository settings (branch protection, private vulnerability reporting). Those are owner decisions.
