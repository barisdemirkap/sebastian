# 0010. Everything the bot sends is logged and can be read back

Status: accepted (owner decision, 2026-11-19).

## Context
Digests and reminders sent by the separate scheduler process (`alfred run`) never reached the message log, so "az önce ne dediydin" or "saat 3 teki mesajı oku" could not find them. A stale button tap made Telegram reject `answerCallbackQuery` ("query is too old") and printed a noisy `callback ack error: HTTPError`. `import-plan` previews expired after 60 minutes, before the owner saw them.

## Decisions
1. **`notify.LoggingNotifier`** wraps the real notifier of `alfred run` and writes each successfully sent message to the message log (a failed send is not logged). The bot's own scheduler already used `ApiNotifier`, which logs.
2. **Read back** (`Assistant._recall`, a read, no model): "az önce ne dediydin", "son mesajı oku", "saat 3 teki mesajı oku" quote the matching message of the bot from the log (hour or hour+12, last 48 hours). Reading back changes nothing: an open preview or question stays. Replies of this feature are not read back themselves.
3. **Callback acks**: an `HTTPError` from `answerCallbackQuery` is expected for stale queries and is handled quietly; the action result is already computed. When the answer could not be shown as a popup, its text is sent as a normal message.
4. **`import-plan --ttl-minutes`** defaults to 1440 (one day).

## Consequences
The log (30 days) now holds digests and reminders; previews quoted back keep working because I10 exempts quotes.
