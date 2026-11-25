# 0004. One isolated instance per user; hosted multi-tenant is not built

Status: accepted (board-revised direction, 2026-11-25). Refines ADR 0001.

## Context
ADR 0001 dropped a hosted multi-user service. The board then revised the direction: keep the forkable single-user core, and make it easy to deploy as a container where each user runs their own isolated instance and supplies only their own model API key.

## Decision
- The unit of deployment is one container (or process) per user, with its own data volume, config and secrets. Nothing is shared between instances.
- The model API key (Anthropic or OpenAI) is the user's own, read from the environment only, never logged or printed, never baked into the image or committed.
- A hosted multi-tenant service is intentionally NOT built. It would need, first: (1) a demand check with real prospective users; (2) a key-custody threat model (we would hold many users' API keys and personal calendar, mail and message data); (3) a legal view (data protection, processor role, terms); (4) a named operator who carries on-call and abuse handling; (5) a costing with kill criteria (hosting, support, model spend, liability).
- Until those exist, "run it for someone else" means helping them deploy their own instance, not operating it for them.

## Consequences
- No tenancy layer, no shared database, no account system, no central key store.
- The model provider interface (`model_providers.py`, selected by `SEBASTIAN_NL`) is how a user picks Claude CLI, Anthropic key, OpenAI key or a local model.
- `sebastian doctor` checks that the key is present and that `.env` is not readable by others, without printing the key.
- Reopening the multi-tenant question requires a new ADR that answers the five items above.
