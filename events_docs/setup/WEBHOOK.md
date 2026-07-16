# Generic inbound webhook (direct — no AP)

Any external system (monitoring, CI, a form, a payment provider) can POST a JSON payload to CUGA and
have an agent triage it — optionally delivering the result to a channel. It's a plain HTTP endpoint
that reuses the `/invoke` seam; **no Activepieces**, no OAuth.

In the trigger registry this is the `inbound` trigger — always live, **pinned** (`?agent=`) or
**routed** (`?route=1` — it lands on the ONE agent, `cuga`, whose supervisor picks the specialist
internally). The only permission is the
optional shared key (`EVENTS_WEBHOOK_KEY`; unset = open).

```
external system ─▶ POST /api/events/hook/<name> ─▶ /invoke(agent) ─▶ (deliver to a channel)
                                                         │
                                              answer returned in the HTTP response
```

The seeded **`incident_triage`** agent is the default: it summarizes an alert, classifies severity
(P1/P2/P3), names the component, and suggests the first action.

## The endpoint
```
POST <EVENTS_PUBLIC_URL>/api/events/hook/<name>
     ?agent=<agent>            # default: incident_triage
     &deliver_to=<channel>     # optional: also post the triage to e.g. slack/discord
     &target=<native id>       # the channel/thread to post to (needed with deliver_to)
     &key=<secret>             # required iff EVENTS_WEBHOOK_KEY is set
Body: any JSON payload.
```

## Example — a monitoring alert → triage → Slack
```bash
curl -s -X POST "https://<tunnel>/api/events/hook/monitoring?deliver_to=slack&target=C0BEYJ9NATB" \
  -H "content-type: application/json" \
  -d '{"alert":"HighCPU","service":"checkout-api","value":"97%","threshold":"85%","runbook":"restart pods"}'
# → {"ok":true,"answer":"HighCPU on checkout-api — P1 — component: checkout-api — first action: restart pods","delivered":true}
```

Point your monitoring tool / CI webhook / form backend at that URL. Other use cases: a CI pipeline
posts a failed build → triage + notify; a payment webhook posts a chargeback → summarize + route; a
contact form posts a lead → qualify + post to a sales channel.

## Security
- Set **`EVENTS_WEBHOOK_KEY`** (TENANT) to require `?key=<secret>` on every call. Without it the
  endpoint is open (fine behind a private network / for a quick demo).
- The `<name>` in the path also acts as a soft namespace — use an unguessable one for a public URL.

## Verify
```bash
# part of the integration e2e:
GATEWAY_TOKEN=<from .env> EVENTS_SERVER_URL=http://localhost:8100 \
  .venv/bin/python tests/events/live_integrations_e2e.py     # includes the WEBHOOK check
```
