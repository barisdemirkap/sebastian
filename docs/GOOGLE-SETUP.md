# Google setup (owner steps, about 20 minutes)

Only you can do this: it needs your Google account. Nothing here is run by an agent. Google changes its console layout from time to time; if a label differs slightly, look for the same word nearby ("Google Auth Platform" was called "OAuth consent screen" before).

What you will end up with: a private Google Cloud project that belongs to you, an OAuth client called "Sebastian", and a token file on your Mac that lets Sebastian read your calendar and, after your "evet", add, change and delete events. Gmail is NOT used now (it can be added later in the same project; it needs a separate decision).

## 1. Create the Google Cloud project
1. Open https://console.cloud.google.com/ and sign in with your own Google account.
2. Top bar, click the project picker (next to the Google Cloud logo), then **New project**.
3. Project name: `sebastian-personal`. Leave organization/location as is. Click **Create**, then select the new project in the picker.

## 2. Enable the Calendar API
1. Menu (three lines) > **APIs & Services** > **Library**.
2. Search `Google Calendar API`, open it, click **Enable**.
3. (Gmail: skip for now. Later: search `Gmail API` and click Enable; it also needs a new scope and an owner decision.)

## 3. Configure the OAuth consent screen
1. Menu > **APIs & Services** > **OAuth consent screen** (new console: **Google Auth Platform**, click **Get started**).
2. App name: `Sebastian`. User support email: your own address. Click **Next**.
3. Audience: **External**. Click **Next**.
4. Contact information: your own email. Click **Next**, accept the policy checkbox, click **Continue**, then **Create**.
5. Left menu **Audience** (old console: first page, "Publishing status"): status must be **Testing**.
6. Under **Test users** click **Add users**, enter **your own Google account address** (the one whose calendar Sebastian will manage), click **Save**. Only listed test users can sign in.
7. Left menu **Data Access** (old console: "Scopes" step) > **Add or remove scopes**. Select (search "calendar", or paste into "Manually add scopes"):
   - `https://www.googleapis.com/auth/calendar.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
   Click **Update**, then **Save**.

## 4. Create the OAuth client (Desktop app)
1. Left menu **Clients** (old console: **Credentials** > **Create credentials** > **OAuth client ID**) > **Create client**.
2. Application type: **Desktop app**. Name: `Sebastian desktop`. Click **Create**.
3. A dialog shows the **Client ID** (ends in `.apps.googleusercontent.com`) and the **Client secret**. Keep this window open or click **Download JSON** and store it somewhere private. The secret is shown again later under the client's page.

## 5. Put the id and secret on your Mac
Never paste them into chat, git, or any file inside the repo.
```
mkdir -p ~/.config/sebastian/secrets && chmod 700 ~/.config/sebastian/secrets
read -s S && printf '%s' "$S" > ~/.config/sebastian/secrets/google.client_secret && unset S
chmod 600 ~/.config/sebastian/secrets/google.client_secret
```
(`read -s` waits for you to paste the secret and press Return; nothing is echoed.)

Config (`~/.config/sebastian/config.toml`, copy from `config.example.toml`): set your address, keep `enabled = false` until step 7, and put the client id (not secret) here:
```
[[accounts]]
provider = "google"
address = "you@gmail.com"
label = "personal"
scopes = ["https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/calendar.events"]
enabled = false

[oauth.google]
client_id = "123456789-abc.apps.googleusercontent.com"
```

## 6. Consent (the OAuth flow)
1. In the repo: `.venv/bin/sebastian auth-url personal`. It prints one long URL (it contacts nobody).
2. Open the URL in a browser where you are signed in as the test user.
3. Google may warn "Google hasn't verified this app": that is normal for your own private project, see "Unverified app warning" below.
4. The consent screen will show Sebastian asking for: "See and download any calendar you can access using your Google Calendar" (`calendar.readonly`) and "View and edit events on all your calendars" (`calendar.events`). Tick the boxes, click **Continue**.
5. The browser then goes to an address starting with `http://127.0.0.1:8765/callback?state=...&code=...` and shows "This site can't be reached". That is expected; nothing is listening there. **Copy the whole address from the address bar.**
6. Back in the terminal (this call contacts Google, so it needs the live flag):
   ```
   SEBASTIAN_ALLOW_LIVE=1 .venv/bin/sebastian auth-finish personal 'PASTE_THE_WHOLE_ADDRESS_HERE'
   ```
   (Keep the quotes: the address contains `&`.) It stores the token as `~/.config/sebastian/secrets/personal.token` (mode 0600).

## 7. Switch it on
1. In `config.toml` set `enabled = true` for the account.
2. Read-only check: `SEBASTIAN_ALLOW_LIVE=1 .venv/bin/sebastian digest morning` prints today's digest.
3. Writes and chat need the env lines from `docs/deploy.md` (`SEBASTIAN_ALLOW_LIVE=1`, `SEBASTIAN_ALLOW_WRITE=1`, `SEBASTIAN_OWNER_CHAT_ID`, `SEBASTIAN_NL=claude`, `SEBASTIAN_NL_CONSENT=1`). Sebastian still asks "evet" before every change.

## Token lifetime (important)
While the app status is **Testing**, Google expires refresh tokens after 7 days for these scopes. Then Sebastian reports calendar errors until you repeat step 6 (`auth-url`, consent, `auth-finish`). Two choices:
- Stay in Testing and repeat step 6 weekly (nothing else changes).
- Audience > **Publish app** (status "In production", still unverified, only you use it): tokens stop expiring after 7 days, and you click through the unverified warning once. You may keep the app unpublished again later. This is your decision; verification by Google is not needed for personal use.

## Unverified app warning
Seen as "Google hasn't verified this app" (or "This app isn't verified"). It appears because you did not submit Sebastian to Google's review, which is unnecessary for an app only you use. Proceed only if the app name is `Sebastian`, the project is yours, and the address bar is `accounts.google.com`:
1. Click **Advanced** (small link at the lower left).
2. Click **Go to Sebastian (unsafe)**.
3. Continue with the checkboxes. If you see "Access blocked: Sebastian has not completed the Google verification process" or "Error 403: access_denied", your account is not on the Test users list (step 3.6): add it and try again.

## Revoke access
- Your Google account: https://myaccount.google.com/permissions > **Sebastian** > **Delete all connections**. Sebastian's token stops working at once.
- On the Mac: delete `~/.config/sebastian/secrets/personal.token` (and `google.client_secret` if you stop using it).
- Cloud console: **APIs & Services > Credentials**, delete the `Sebastian desktop` client, or delete the whole `sebastian-personal` project (Menu > IAM & Admin > Settings > Shut down).
- Sebastian side: set `enabled = false` in `config.toml`, or remove `SEBASTIAN_ALLOW_WRITE=1` to keep reading but never write.
