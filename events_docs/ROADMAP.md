# Roadmap

Where the work is headed. Phase definitions and current status are in [PHASES.md](PHASES.md); this is
the sequenced "what's next." We are in **P3 (Run) = MVP, ~75%**.

## Finish P3 (MVP) — the near work

1. **NL→flow rigor** *(the strategic priority)*. Make an English sentence → the right flow
   *measurable*, not just demoable:
   - a typed **FlowSpec** intermediate the concierge emits;
   - a **validation gate** that checks a FlowSpec before arming (right agent, right trigger, right sink);
   - a **labeled benchmark** (utterance → expected FlowSpec) scored in CI;
   - a **model bake-off** for the concierge (accuracy / latency / cost).
   - Then **branching/ROUTER flows** (the builder is linear today).
2. **Webhook — OUT** — deliver an answer to any HTTP endpoint (optional HMAC). Unlocks flow→flow
   chaining — a capability, not just another connector.
3. **Email delivery sink** — "…email me the brief" delivers to an inbox, not a chat.

## P4 · Sprint — breadth (no new architecture)

WhatsApp · Email-as-a-channel · Google Calendar · Drive/Sheets · one work tool (Notion/Jira/Linear) ·
RSS/feeds. Each is a new connector on the existing seam.

## P5 · Fly — cloud & scale

Multi-tenant cloud: real isolation, IdP/OIDC, managed infra, stable URLs, a **secrets vault**
(replace `.env`), observability, horizontal scale.

## Backlog (beyond Fly)

Twilio SMS · Stripe · Salesforce/HubSpot · PagerDuty/Datadog · Dropbox/S3 · MS Teams · Signal.

## Recently shipped

All 4 channels live · Gmail/GitHub/Box PUSH proven end-to-end · generic webhook-IN · the Box download
step · GitHub OAuth (replacing the broken PAT path) · debug fire (`/subscriptions/{id}/run`) ·
`?flow=1` returns the armed flow · credential rotation · the `test-fire` harness + consolidated report.
