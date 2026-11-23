# Phase 3 design: WhatsApp Business (read-only unanswered digest)

Status: design plus offline adapter only. Nothing is registered, connected, or sent. Whether Phase 3 is still wanted is an owner decision, and a separate risk review is required first.

## What exists (offline)
`src/sebastian/whatsapp.py`: `WhatsAppReader` interface (read-only, no send method), `parse_webhook` for Cloud API webhook bodies, `FixtureWhatsAppReader`, `unanswered_chats` and a digest formatter. Message text is discarded at parse time; chat ids are masked in output. Tests: `tests/test_phase2_3.py`.

## Architecture
- The official Cloud API is push-based: inbound messages arrive as webhooks to an HTTPS endpoint; there is no history polling. Live use therefore needs a public webhook receiver that verifies the Meta signature, appends metadata to a local file, and feeds `WhatsAppReader`. The receiver is not built: it needs hosting (spend) and exposure of a public endpoint.
- Outbound messages sent by the business number are only visible if sent via the API or reported by status webhooks, so "unanswered" can be wrong when someone replies from the Business app. Treat results as hints.
- The digest section reuses the same pipeline as mail (`WhatsApp Business needs reply:`), including the 24-hour customer-service window flag.

## Risk review (for the owner)
- Policy: Business API accounts require a business, a verified Meta Business portfolio, and a dedicated phone number not registered in the personal WhatsApp app (registration is irreversible for that number). Personal WhatsApp stays out of scope (cancelled).
- Cost: conversation pricing applies to business-initiated messages; read-only receiving is low or no cost, but webhook hosting costs money. All spend needs approval.
- Data handling: third-party customer messages are personal data. Keep metadata only, no bodies, local processing, redacted logs, retention of a few days, and check privacy-law and consent obligations (for example GDPR or KVKK) for the owner's use.
- Security: webhook signature verification (`X-Hub-Signature-256`), verify token, secrets in `~/.config/sebastian/secrets/`, long-lived system-user token stored 0600.
- Writes: replies, templates, and read receipts are sends to third parties. They need a new ADR superseding ADR 0001, and even then drafts only.

## Owner decisions (owner)
1. Is Phase 3 still wanted?
2. Does a business and a dedicated number exist, and may one be registered?
3. Approve hosting for the webhook receiver and its cost.
4. Approve the risk review above.
