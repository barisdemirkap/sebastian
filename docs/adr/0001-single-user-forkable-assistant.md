# 0001. Single-user, forkable, local-first assistant

Status: accepted (owner decision, 2026-11-23).

## Context
The goal was first framed as a hosted, multi-user, end-user-ready product. A board review (CEO, CTO, CFO, CPO, Investor) objected to committing to multi-user hosting without demand evidence, cost figures or kill criteria.

## Decision
Sebastian is a personal assistant for one user, with a generic structure: anyone forks it, runs it locally, and configures, customizes and controls it. There is no hosted multi-user service, no tenancy layer and no hosting spend.

## Consequences
- Personal data lives only in the user's config and local data dir, never in the repository.
- Integrations are plugins behind interfaces; writes are preview-gated and need explicit confirmation.
- User-facing text is a swappable language pack.
- The earlier "multi-user" phase is dropped.
