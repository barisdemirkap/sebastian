# 0001. Standalone, read-only, approval-gated design

Status: accepted (2026-11-18)

## Context
Alfred touches personal and company calendars and, later, mail. Mistakes there are costly and hard to reverse. It was started alongside another private project but has a different purpose and lifecycle.

## Decision
1. Standalone: separate repo, no shared code or imports with that project. Only a Telegram bot token and owner chat id connect it to the outside, via the `Notifier` interface.
2. Read-only: minimal read scopes only; scopes are validated in code; no writes to calendars or mailboxes; reply drafts are suggestions, never sent.
3. Approval-gated: OAuth consent, third-party sends, and spend each need explicit owner approval. Live transport is off unless `ALFRED_ALLOW_LIVE=1` and the account is `enabled`.
4. Tests use fixtures and fake transports; no real account is needed.

## Consequences
- Safe to develop without credentials; owner steps are explicit in `docs/ROADMAP.md`.
- Some duplication with it is accepted to keep the projects independent.
- Features requiring write access (auto-reply, calendar edits) need a new ADR superseding this one.
