# 0005. Bot runtime: message log, dedupe, recovery, heartbeat, watchdog, voice

Status: accepted (owner decision, 2026-11-18)

## Decisions
1. **One process.** `alfred bot` long-polls Telegram (25 s), answers the owner, ticks the scheduler (digests, reminders, 22:00 plan) and writes a heartbeat each loop. It needs `ALFRED_ALLOW_LIVE=1`, `ALFRED_OWNER_CHAT_ID`, and the token file `~/.config/alfred/secrets/telegram.token` (read at runtime only, never logged).
2. **Owner only.** Updates from any other chat are dropped without a reply, a log row or any processing. The owner id comes from the environment and appears nowhere in the code.
3. **Message log** (SQLite `~/.config/alfred/alfred.db`, mode 0600): every inbound and outbound message, 30-day retention (purged once a day). It is the owner's own chat history on his own Mac; the audit log holds no message text.
4. **Dedupe and recovery.** An inbound message is logged before it is handled and the last `update_id` is stored; an update with `update_id <= last` is ignored (redelivery, restart). At startup, inbound messages that were logged but never answered (crash, outage) from the last 2 hours are answered once; a message that already failed twice is dropped. Stale pending actions are protected by their 10-minute TTL.
5. **Scheduler robustness.** Each digest or reminder is marked sent and saved right after it was delivered; a delivery failure leaves only that message unmarked, so the next tick retries only it.
6. **Heartbeat + watchdog.** The bot writes the epoch seconds to `~/.config/alfred/heartbeat` (atomic). `scripts/watchdog.sh` (run every 2 minutes by its own launchd job) restarts `com.alfred.bot` through `launchctl kickstart -k` when the heartbeat is older than 180 s or missing. Both launchd files in `deploy/` are templates; nothing installs them.
7. **Voice (opt-in, `ALFRED_VOICE=1`).** Input: Telegram voice note, ffmpeg to 16 kHz wav, `whisper-cli` Turkish model (`ALFRED_WHISPER_MODEL`), then the same path as typed text, preceded by the echo "Şöyle anladım: ...". Output: replies to a voice message are also sent as a short voice note (macOS `say -v Yelda`, ffmpeg to opus); only short replies are spoken, never the echo. All local, nothing leaves the Mac, limits 120 s / 5 MB, failures degrade to text.
8. **Live gates.** The real Telegram transport, like the Google transport, refuses without `ALFRED_ALLOW_LIVE=1`.

## Consequences
A Mac that sleeps misses reminders; the watchdog cannot help then (see `docs/deploy.md`). Crash loops are bounded (attempt cap, launchd throttle).
