# 0002. Multi-channel through adapters (proposed)

Status: accepted (owner, 2026-11-23). Slice 1 done: `channels/base.py` contract, `ConsoleChannel`, contract tests. Next: Slack (Socket Mode) adapter with a fake transport.

## Context
The assistant must be reachable from several platforms (Telegram, Slack, and others), not tied to one.

## Proposal
- A `Channel` interface: receive a message (text, button tap, attachment), send a reply (text, buttons, files), and identify the owner. The assistant core never imports a platform SDK.
- First adapters: Telegram (port from the private assistant) and Slack (Socket Mode: no public URL, runs locally). A CLI/stdin adapter for tests and development. Later: Discord, Matrix, email, web.
- Owner identity is per channel and set in the config (allowlist). Unknown senders are ignored.
- The preview + explicit confirmation gate is channel-independent; buttons degrade to typed replies where a channel has none.
- Conformance tests run against every adapter with a fake transport, no real network.
- Best practices: provider/channel/plugin interfaces, one config file, secrets outside the repo, structured logs without message text, retry queue, idempotent update handling.

## Consequences
- Adds step 2b to the roadmap, after core extraction.
