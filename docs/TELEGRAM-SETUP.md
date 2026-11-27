# Telegram setup (long polling, runs on your machine)

1. In Telegram, talk to **@BotFather**, send `/newbot`, and copy the bot token it gives you.
2. Find your own numeric chat id (for example with a bot such as @userinfobot, or from the `getUpdates` result after you message your bot).
3. Put the token in your secrets directory, never in the repository:

```bash
printf '%s' '123456:ABC...' > ~/.config/sebastian/secrets/telegram.token && chmod 600 ~/.config/sebastian/secrets/telegram.token
```

4. Select the channel in your config:

```toml
[channel]
kind = "telegram"
```

5. Check what is missing, then start the bot (it talks to Telegram only when you allow live access):

```bash
sebastian doctor
SEBASTIAN_ALLOW_LIVE=1 SEBASTIAN_OWNER_CHAT_ID=<your chat id> sebastian bot
```

Only messages from `SEBASTIAN_OWNER_CHAT_ID` are handled; everything else is ignored. Writes show a preview with Save / Cancel buttons and need a tap or a typed confirmation, exactly as on every other channel.
Status: the adapter is covered by offline tests with a fake transport; it runs unchanged from the private assistant it was ported from.
