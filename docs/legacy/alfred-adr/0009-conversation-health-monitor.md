# 0009. Conversation health monitor

Status: accepted (owner decision, 2026-11-19). Ported in spirit from Acme ADR 0015; no shared code.

## Context
Without the owner noticing, nothing tells us the owner is stuck in a loop with the bot.

## Decisions
1. **Signals**, computed from code state after each handled update, names only: S1 the repeat guard fired; S2 three or more consecutive unclear replies; S3 a pending preview expired or failed verification; S4 a frustration phrase (`i18n.FRUSTRATION`, whole words); S5 the model chain failed or broke the schema; S6 handling one update took over 8 s; REPORT from `/bildir`.
2. **Incident**: S2, S4, or two or more signals inside 10 minutes. The owner gets ONE way-out menu (`health_menu`), always the last reply so the preview keeps its inline keyboard, at most once per 30 minutes. It never changes a pending preview. Alfred has one owner, so the menu is also the owner notice: there is no separate admin chat and a stranger's message records nothing.
3. **Storage**: table `health_events(id, chat_id, at, signal, detail)` with no message text; `detail` is a fixed code. It is part of `store.SCHEMA` (`CREATE TABLE IF NOT EXISTS`, so opening an existing database adds it idempotently). Purged after 30 days with the message log.
4. **Commands**: `/bildir` records REPORT and acknowledges (one per 10 minutes, then "already reported"); `/saglik` shows the last 24 hours' counts. `scripts/health_report.py [--hours N] [--db path]` prints a plain-English summary from a read-only connection.

## Consequences
Frustration phrases are a fixed list: a miss costs nothing, a false hit adds one short menu. Slash commands record flags but never get a menu.
