# Gmail setup (Activepieces backend)

Gmail is an **integration**, so it runs on **Activepieces** — and it's the one where AP genuinely
earns its keep: AP does the **OAuth consent + token refresh**, so you never touch a refresh token.
Gmail is both a **PUSH source** (`new_email`) and an **email delivery sink** (`send_email`).

```
new email  ─▶ AP gmail trigger (OAuth) ─▶ /invoke (mailbot) ─▶ deliver
brief/verdict ─▶ /invoke ─▶ AP gmail send_email ─▶ your inbox     (email as a sink)
```

Seeded agents: **`mailbot`** (summarize/triage your Gmail) and **`resume_judge`** (Box → judge →
email). Both are **per-user** — each employee logs into *their own* Gmail.

## What you'll need
- A **Google Cloud OAuth 2.0 client** (client id + secret) with the Gmail scope.
- A public HTTPS URL (`EVENTS_PUBLIC_URL`) for the OAuth redirect + the AP webhook.

## Steps
1. **Create an OAuth client** — Google Cloud Console → *APIs & Services → Credentials → Create OAuth
   client ID* (Web application). Enable the **Gmail API**. Add the redirect URI:
   `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback`.

2. **Add the client creds** to `.env` (or the Studio → Admin → OAuth-apps panel):
   ```
   EVENTS_OAUTH_GMAIL_CLIENT_ID=…apps.googleusercontent.com
   EVENTS_OAUTH_GMAIL_CLIENT_SECRET=…
   ```
   Restart the server.

3. **Each user logs in** (consent — a pasted token will NOT work; AP does the code exchange + refresh):
   ```
   open  GET /api/events/connect/gmail        # → Google consent → callback → AP connection
   ```

4. **Arm a watcher / use email delivery** — ask the concierge:
   *"when an email from my boss arrives, summarize it and message me"* (PUSH), or
   *"every day at 8am email me a market brief"* (email as a delivery sink).

## Verify
```bash
# full integration e2e (NOW/CRON/POLL + PUSH box/github/gmail):
GATEWAY_TOKEN=<from .env> EVENTS_SERVER_URL=http://localhost:8100 \
  .venv/bin/python tests/events/live_integrations_e2e.py
```
With Gmail connected, the `PUSH · gmail` leg **arms a real AP flow** (an `ap_flow_id` appears). Then
send yourself a matching email → a summary is delivered.

## Troubleshooting
- **`gmail isn't configured for OAuth on this deployment`** — `EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET`
  aren't set. Add them (step 2) and restart.
- **`CONNECT NEEDED — connect your gmail`** — creds are set but this user hasn't logged in; open
  `GET /api/events/connect/gmail`.
- **`redirect_uri_mismatch`** — the redirect URI in Google Cloud must exactly match
  `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback` (https, no trailing slash).
- **Token expired** — it shouldn't: AP refreshes it. That's the whole reason Gmail stays on AP.
