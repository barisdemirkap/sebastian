#!/usr/bin/env bash
# Sebastian watchdog (TEMPLATE-LEVEL tool: the owner schedules it, see deploy/com.sebastian.watchdog.plist.template).
# The bot writes the current epoch seconds to the heartbeat file every loop (at most ~30 s apart). If that value is
# older than SEBASTIAN_STALE_SECONDS the bot is stuck or dead, and launchd is asked to restart it.
#
# Env:  SEBASTIAN_HEARTBEAT        heartbeat file     (default ~/.config/sebastian/heartbeat)
#       SEBASTIAN_STALE_SECONDS    allowed age        (default 180)
#       SEBASTIAN_LAUNCHD_LABEL    launchd label      (default com.sebastian.bot)
#       SEBASTIAN_WATCHDOG_DRYRUN  1 = only print what would be done (used by tests)
# Exit: 0 healthy, 1 stale (restart requested), 2 heartbeat missing (restart requested).
set -u
HB="${SEBASTIAN_HEARTBEAT:-$HOME/.config/sebastian/heartbeat}"
STALE="${SEBASTIAN_STALE_SECONDS:-180}"
LABEL="${SEBASTIAN_LAUNCHD_LABEL:-com.sebastian.bot}"
NOW="${SEBASTIAN_WATCHDOG_NOW:-$(date +%s)}"

restart() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') watchdog: $1; restarting $LABEL"
  if [ "${SEBASTIAN_WATCHDOG_DRYRUN:-0}" = "1" ]; then
    echo "DRYRUN: launchctl kickstart -k gui/$(id -u)/$LABEL"
  else
    launchctl kickstart -k "gui/$(id -u)/$LABEL" || echo "watchdog: kickstart failed"
  fi
}

if [ ! -f "$HB" ]; then
  restart "heartbeat file missing"
  exit 2
fi
LAST="$(tr -dc '0-9' < "$HB")"
if [ -z "$LAST" ]; then
  restart "heartbeat unreadable"
  exit 2
fi
AGE=$((NOW - LAST))
if [ "$AGE" -gt "$STALE" ]; then
  restart "heartbeat ${AGE}s old (limit ${STALE}s)"
  exit 1
fi
exit 0
