# Slack setup (Socket Mode, runs on your machine)

1. Create a Slack app (api.slack.com/apps), enable **Socket Mode**, and create an app-level token (`connections:write`).
2. Bot token scopes: `im:history`, `im:write`, `chat:write`, `files:read`. Subscribe to the bot event `message.im`. Enable **Interactivity** (no URL needed in Socket Mode).
3. Install the app to your workspace. Copy your own Slack user id (profile > More > Copy member ID).
4. Put both tokens in your secrets directory (never in the repository) and the user id in your config.
5. `pip install websockets`, then build the channel:

```python
from sebastian.channels import SlackChannel
from sebastian.channels.slack import web_api_caller
from sebastian.channels.slack_socket import SocketModeEvents
channel = SlackChannel(owner_chat=1, owner_user="U0123456", call=web_api_caller(bot_token), events=SocketModeEvents(app_token))
```

Only direct messages from `owner_user` are handled; everything else is ignored. Writes still show a preview with buttons and need a tap or a typed confirmation.
Status: the translation layer is covered by offline tests; the live Socket Mode transport has not been run against a real workspace yet.
