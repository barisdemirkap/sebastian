# Deployment recipe (launchd templates, never installed by agents)

Nothing here is run by agents. The owner performs every step. Two ways to run:
- `sebastian bot` (recommended): Telegram assistant + scheduler + heartbeat in one process (ADR 0005). Template: `deploy/com.sebastian.bot.plist.template`, watchdog: `deploy/com.sebastian.watchdog.plist.template`.
- `sebastian run`: scheduler only (digests and reminders, no chat). Template: `deploy/com.sebastian.scheduler.plist.template`.

## Steps for the bot (owner)
1. Try it without any network: `sebastian demo morning` and `sebastian chat` (fixture calendar, nothing saved).
2. Do `docs/GOOGLE-SETUP.md` (or `docs/GOOGLE-SETUP.tr.md`): OAuth client, consent, `sebastian auth-url personal`, `sebastian auth-finish`. Then set `enabled = true` for the account in `~/.config/sebastian/config.toml`.
3. Create `~/.config/sebastian/env` from `deploy/sebastian.env.example`, `chmod 600`. Required lines:
   ```
   SEBASTIAN_OWNER_CHAT_ID=<your telegram chat id>
   SEBASTIAN_ALLOW_LIVE=1
   SEBASTIAN_ALLOW_WRITE=1
   SEBASTIAN_NL=claude
   SEBASTIAN_NL_CONSENT=1
   ```
   The bot token stays in `~/.config/sebastian/secrets/telegram.token` (0600).
   Optional agent mode pilot (ADR 0019): `SEBASTIAN_NL=agent` (with the consent line). Rollback: `SEBASTIAN_NL=claude` and restart, or write `/ajan kapat` to the bot (instant). Check first with `.venv/bin/python scripts/agent_smoke.py` (3 model calls).
4. First run by hand: `scripts/run-bot.sh`. Write "bugün ne var" to the bot, then try an add and answer "evet".
5. Copy `deploy/com.sebastian.bot.plist.template` to `~/Library/LaunchAgents/com.sebastian.bot.plist`, replace `__REPO_DIR__` and `__HOME__`, then `launchctl load ~/Library/LaunchAgents/com.sebastian.bot.plist`. Logs: `~/Library/Logs/sebastian-bot.log` and `.err.log`.
6. Watchdog: same for `deploy/com.sebastian.watchdog.plist.template` (`com.sebastian.watchdog`). It restarts the bot when `~/.config/sebastian/heartbeat` is older than 180 s.

Uninstall: `launchctl unload` both files and delete them. A Mac that sleeps at 08:30 or at a reminder time sends late or not at all (digests have a 2-hour catch-up; reminders go out until the event starts); a small always-on host would need spend approval.
