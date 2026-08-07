# WhatsApp setup (direct backend — the only one)

WhatsApp talks to CUGA **directly** over Meta's Cloud API: Meta POSTs each inbound message to a CUGA
endpoint, and CUGA replies through the Graph API. There is no Activepieces option — AP's WhatsApp
piece is **send-only** (0 triggers, 3 actions), so it cannot receive, and a channel needs both halves.

```
your phone ─▶ Meta Cloud API ─▶ POST /api/events/whatsapp/events (CUGA)
                                            │
                                     /run (CUGA's door)
                                            │
                          POST {PHONE_NUMBER_ID}/messages ◀── the reply
```

> **WhatsApp is 1:1 by construction.** A conversation with your business number is always exactly one
> human, so `wa_id` (their phone number) is both the delivery address and the per-user identity. No
> `channel_user_id` disambiguation, unlike Slack where many people share one channel.

## What you'll need

- A **Meta Business Platform** number — *not* the WhatsApp Business App. See the fork below.
- A public HTTPS URL for CUGA (`EVENTS_PUBLIC_URL`) — see [README.md](README.md) prerequisites.

### The fork that catches everyone

| | Groups | API | Who responds |
|---|---|---|---|
| WhatsApp **Business App** (green phone app) | ✅ | ❌ **none** | a human, typing |
| WhatsApp **Business Platform** / Cloud API ← *this one* | via Groups API (OBA only, ≤8) | ✅ | **your webhook, i.e. CUGA** |

**One number can serve one of these, not both.** Moving a number onto the Cloud API means losing the
green app on it — chats and mobile interface included. Most people register a *separate* number for
the API rather than sacrifice the one they already use.

## Steps

1. **Create the app** — <https://developers.facebook.com/apps> → **Create app** → use case
   **“Connect with customers through WhatsApp”**.

2. **WhatsApp → API Setup** — select or create a WhatsApp Business account. Save the **WABA ID**.

3. **Test number** — from the *From* dropdown, add the free test number. Save the
   **Phone number ID**. Add your own phone under *To* (the allow-list; a test number may only
   message numbers you list here).

4. **Get a token.** Two options — read this before clicking:

   | | Where | Lifetime |
   |---|---|---|
   | “Generate access token” on API Setup | developer console | **24 hours** |
   | **System User token** ← use this | **Business Manager**, not the dev console | does not expire |

   **System users are not in the developer console.** They live in Business Manager:
   <https://business.facebook.com/settings/system-users> — or Meta Business Suite → portfolio
   dropdown → **Settings** → **Users → System users**.

   Then: **Add** (role *Admin*) → **Assign Assets** → your app (*Full control*) **and** your WABA
   (*Manage WhatsApp Business accounts*, Full control) → **Generate token** → select the app → tick
   `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`.

   > If **System users** isn’t visible you have no *business portfolio* — they only exist inside one.
   > A WhatsApp setup spun up from the dev console often has none attached. Create a portfolio and
   > attach the app + WABA, or use the 24-hour token to get going and come back to this.

   Meta’s own quickstart curls the **test** number with a **System User** token, so there is no stage
   at which you need the temporary one. Build against the permanent token from the start — a
   short-lived credential’s expiry resurfaces later as a mystery `401`.

5. **Add to `.env`**:
   ```
   WHATSAPP_TOKEN=EAAG…                    # System User token
   WHATSAPP_PHONE_NUMBER_ID=123456789012345
   WHATSAPP_APP_SECRET=…                   # App settings → Basic → App secret
   WHATSAPP_VERIFY_TOKEN=…                 # any random string YOU invent
   # optional — only needed for sends OUTSIDE the 24-hour window (see below)
   WHATSAPP_TEMPLATE_NAME=
   WHATSAPP_TEMPLATE_LANG=en_US
   WHATSAPP_API_VERSION=v23.0
   ```
   All read through `secret_seam`, so `WHATSAPP_TOKEN=vault://events/whatsapp` works. Restart the
   server afterwards.

6. **Configure the webhook — this is TWO steps, and skipping the second is the classic failure.**

   Meta app → **WhatsApp → Configuration → Webhook**:

   a. **Callback URL** = `<EVENTS_PUBLIC_URL>/api/events/whatsapp/events`
      **Verify token** = your `WHATSAPP_VERIFY_TOKEN` → **Verify and save**.

   b. **Manage** → tick the **`messages`** field → Done.

   c. **Subscribe your app to the WABA.** This is a *third* thing, at the account level, and it is
      the one most often missed:
      ```bash
      curl -X POST "https://graph.facebook.com/v23.0/<WABA_ID>/subscribed_apps" \
           -H "Authorization: Bearer $WHATSAPP_TOKEN"
      # → {"success": true}
      ```
      No body — Meta infers the app from the token. Check it with a `GET` on the same path.

   > **All three are required, and they mean different things:**
   > (a) *can Meta reach you* · (b) *which events you want* · (c) *for which account*.
   >
   > With (a) and (b) done but (c) missing, verification succeeds, the logs show a lone `GET … 200`,
   > and every message vanishes — no inbound POST ever arrives.
   >
   > It is invisible because Meta's guided onboarding usually does (c) for you, and because the
   > console's own **Send message** test button runs through Meta's internal
   > `WA DevX Webhook Events 1P App` — which *is* subscribed. So the console test works while your
   > callback gets nothing. `GET /<WABA_ID>/subscribed_apps` listing only that 1P app and not yours
   > is the tell.

   **Is (c) a code step?** No — for a single-tenant deploy it is one-time account configuration, like
   inviting a Slack bot to a channel. It would only become code in a multi-tenant product, where
   customers onboard their own numbers via Embedded Signup and you `POST subscribed_apps` as part of
   that flow.

7. **Verify before testing from your phone** — this is exactly what Meta does on *Verify*:
   ```bash
   BASE=https://<your-events-url>
   # wrong token must be REFUSED (403)
   curl -s -o /dev/null -w '%{http_code}\n' \
     "$BASE/api/events/whatsapp/events?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=x"
   # correct token must echo the challenge as PLAIN TEXT (not JSON)
   curl -s "$BASE/api/events/whatsapp/events?hub.mode=subscribe&hub.verify_token=$WHATSAPP_VERIFY_TOKEN&hub.challenge=probe123"
   # → probe123
   # and whatsapp must appear in the channel registry
   curl -s "$BASE/api/events/channels" | grep -o whatsapp
   ```
   The refusal check matters as much as the happy path: a webhook that accepts anything looks like it
   works while being open to anyone who finds the URL.

8. **Send “hi” from your allow-listed phone.** Watch it land:
   ```bash
   make ce-logs GREP=whatsapp     # on Code Engine
   make logs                      # locally
   ```

## The 24-hour window — the thing that makes WhatsApp unlike every other channel

Free-form text is only permitted **within 24 hours of the user’s last inbound message**. Outside it
Meta *rejects* the send, and only a pre-approved **template** may be sent.

```
Mon 15:00  user messages you            → window opens
Tue 09:00  your cron fires (18h later)  → ✅ free-form
Tue 15:00                               → window closes
Wed 09:00  your cron fires (42h later)  → ❌ REJECTED
```

CUGA tracks `last_inbound_at` per `wa_id` and `whatsapp_direct.send_message` picks the mode, so
nothing else in the events layer needs to know. But note the consequence:

> **The better the agent works, the more likely this breaks.** A user who never needs to ask never
> messages the bot, so their window is never open — and the 09:00 digest fails for exactly the users
> getting the most value.

A template is *registered text with numbered slots*, so an agent’s free-form answer cannot ride it.
The usual pattern is a nudge (“your digest is ready — reply to see it”) that reopens the window, then
the full answer. Get one **utility** template approved early; review takes hours to days.

### Testing the closed-window path

While developing you message the bot constantly, so the window is always open and the template branch
is **dead code that looks alive**. Force it:

```
WHATSAPP_FORCE_TEMPLATE=1
```

With no `WHATSAPP_TEMPLATE_NAME` set, an out-of-window send fails **loudly**
(`outside the 24h window and no WHATSAPP_TEMPLATE_NAME set`) rather than going silent.

## Deploying to Code Engine

`deploy/ce/make_env_ce.sh` builds the CE secret from an **explicit key allowlist**. The
`WHATSAPP_*` keys are on it — but if you add new ones, add them there too, or the deploy succeeds,
the route answers, and every send fails with `no WHATSAPP_TOKEN` while the code looks correct.

```bash
cd deploy/ce && ./make_env_ce.sh      # regenerate .env.ce from .env  (chmod 600, gitignored)
make ce-build && make ce-deploy       # rebuild + redeploy
```

Changing only a *value* in `.env` needs no rebuild — regenerate `.env.ce`, re-sync the secret, and
bounce the revision.

## Going to production — what the test number is, and what it isn't

The number Meta lends you (`+1 555-…`) is **a development fixture, not a business line.**

| | Test number | Your own number |
|---|---|---|
| Who owns it | **Meta** — lent, not transferable | you |
| Who it can message | **only your allow-list** (a handful) | anyone who messages you |
| Cost | free | per-message, by category |
| Can you publish it | **no** — it ignores everyone not on the list | yes |

The `+1 555-` prefix is the giveaway: that range is reserved for fictional US numbers. Publishing it
would hand out a number that cannot reply to strangers.

### The move to a real number

Your **code does not change** — only `WHATSAPP_PHONE_NUMBER_ID` and the token. Everything else
(payload shape, signature, send endpoint, the 24-hour rule) is identical.

1. **Get a number you own** — mobile or landline that can receive an SMS/voice code, and that is
   **not already registered to any WhatsApp account** (personal or Business App).
2. **Add it to your WABA** in WhatsApp Manager → **verify ownership** by code.
3. **Register it**: `POST /{PHONE_NUMBER_ID}/register` with a 6-digit PIN (two-step verification).
4. **Display name approval** — the name customers see; Meta reviews it.
5. **Business Verification** — Meta checks the business is real (documents, domain).
6. **Re-do webhook step 6c for the new WABA** if it differs — `subscribed_apps` is *per account*.
7. **Messaging tiers** begin: you start capped at a few hundred unique customers per rolling 24h and
   scale as your quality rating holds. A poor quality rating throttles you.

### Discovery — there is none

**WhatsApp has no directory.** Nobody can search for your business. Every route to your number is one
you build:

- `wa.me/<number>` links — site, email signature, anywhere
- **QR codes** — the standard for physical spaces
- **Click-to-WhatsApp ads** on Facebook/Instagram — Meta's actual discovery product, and how most
  volume arrives
- simply telling people the number

So "publish my business on WhatsApp" is not something you do *inside* WhatsApp. It is marketing
elsewhere that points at a number.

The blue-tick **Official Business Account** badge is separate again — a notability review, not an
application. It also gates the Groups API.

### Before you publish — the isolation prerequisite

While only your allow-listed phone can reach the test number, `user_id="local"` is *you*, and nothing
can leak. **A public number removes that protection**: any stranger who messages it resolves to the
same `local` principal — the one holding whatever credentials you connected. See the closing section.

## “Watching” WhatsApp — what a trigger can and cannot be

> **Not implemented today.** WhatsApp inbound currently goes to CHAT only (`NOW` mode): the route
> answers through CUGA's door. It does **not** dispatch to watchers the way Slack does, so
> *“when someone messages us with X, do Y”* will not fire yet. Everything below is what the shape
> would be.

First, the hard boundary: **you can only watch messages sent to your own business number.** There is
no “watch my WhatsApp for messages from my boss” — you cannot read a personal WhatsApp account. This
is what makes WhatsApp a *channel*, never an *integration*.

Within that boundary there are two useful trigger sources:

| Source | Example | Status |
|---|---|---|
| **inbound messages** to your number | “when someone messages us with *refund*, triage it and reply” | would mirror Slack's `new_channel_message` — needs a `direct_events.match(store, "whatsapp", …)` call in the POST route |
| **account webhook fields** you are already subscribed to | “tell me when a template is approved or rejected”; “alert me if the number's quality rating drops” | `message_template_status_update`, `phone_number_quality_update`, `account_alerts` — they arrive at the same webhook today and are **parsed away**, because `messages()` only extracts `messages[]` |

That second row is worth noting: those events are already being delivered and silently dropped.
Template approval and quality-rating alerts are genuinely useful operational triggers — a quality
drop throttles your sending, and you would want to know before it does.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `404` on the callback URL | the deployed image predates the WhatsApp channel — rebuild and redeploy |
| Meta says “callback URL could not be validated” | the route must return the challenge as **plain text**; JSON fails. Also check `WHATSAPP_VERIFY_TOKEN` matches exactly |
| Verification succeeded, but **messages never arrive** | the **`messages` field is not subscribed** — step 6b. Logs show the `GET … 200` and no POSTs |
| `Error validating access token: Session has expired` | you used the 24-hour token — switch to a System User token (step 4) |
| Every send fails `no WHATSAPP_TOKEN` on CE but works locally | the key isn’t in `make_env_ce.sh`’s allowlist |
| Inbound `401 bad signature` | `WHATSAPP_APP_SECRET` mismatch. The HMAC is over the **raw** body — a proxy that re-serialises JSON breaks it |
| Bot replies to its own messages | shouldn’t happen: `statuses[]` (delivery receipts) are ignored. If it does, check the payload parser |
| Scheduled fire produces an answer but the user hears nothing | outside the 24-hour window with no template configured — see above |

## What WhatsApp is (and isn’t) good for

| CUGA mode | Verdict | |
|---|---|---|
| **NOW** — you ask, it answers | ✅ ideal | free-form, window open |
| **PUSH** — inbound message triggers work | ✅ ideal | the customer-service shape |
| **CRON** — one digest a morning | ⚠️ workable | template outside the window; ~1/day is fine |
| **POLL** — check every 10 minutes | ❌ wrong channel | ≈4,300 billed messages/user/month, and Meta reads it as spam |
| **WEBHOOK** — external event → notify | ⚠️ volume-dependent | fine if rare |

High-frequency proactive push is blocked by **economics and policy**, not by the API. For “CUGA does
things for me on a schedule”, use Telegram or Slack — free and unrestricted. WhatsApp earns its place
when CUGA faces **customers**.

## Before the number is public

A WhatsApp business number is **reachable by anyone**. An unlinked sender currently resolves to
`user_id="local"` — the same principal that holds whatever credentials you connected during setup. So
a stranger asking “summarise my email” would read *yours*. Slack at least requires workspace
membership; WhatsApp removes that barrier entirely.

**Populate the identity map (or make the unlinked path fail closed) before publishing the number.**
Grep for `channel.unlinked` in the logs to see it happening.

## Related

- [README.md](README.md) — prerequisites shared by every connector
- [NGROK.md](NGROK.md) — a public URL for local development
- [TELEGRAM.md](TELEGRAM.md) — the unrestricted 1:1 alternative
