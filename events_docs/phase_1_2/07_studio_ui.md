# 07 — Studio UI (dumb): reads + concierge chat

The Studio is added into CUGA's existing React frontend. It is **dumb**: each tab is a
`GET /api/events/*` fetch → render; the Concierge tab POSTs text. No client-side logic — status,
catalog, and decisions all come from the server. Full detail: [../STUDIO_UI.md](../STUDIO_UI.md).
Endpoints in [`app.py`](../../src/cuga/backend/events/app.py); descriptors in
[`connectors.py`](../../src/cuga/backend/events/connectors.py) / [`catalog.py`](../../src/cuga/backend/events/catalog.py).

```mermaid
sequenceDiagram
    autonumber
    participant Br as Browser<br/>(CUGA React — StudioPage.tsx)
    participant API as CUGA FastAPI<br/>(events endpoints)
    participant EN as APEngine
    participant DB as SubscriptionStore
    participant CO as Concierge

    Note over Br: gate — is the Studio even shown?
    Br->>API: GET /api/events/status
    API-->>Br: {enabled, scope, ap_configured, features}   (null → hide Studio)

    par Channels tab
        Br->>API: GET /api/events/channels
        API-->>Br: web/telegram/discord/slack + status (bot-token presence)
    and Integrations tab
        Br->>API: GET /api/events/integrations
        API->>EN: list_connections(project = principal scope)
        EN-->>API: live AP connections
        API-->>Br: gmail/box/github/slack + connected? + connect_url
    and Flows tab
        Br->>API: GET /api/events/subscriptions
        API->>DB: as_dicts(scope)
        DB-->>Br: armed watchers (NOW/CRON/PUSH/POLL badges)
    and Examples tab
        Br->>API: GET /api/events/examples
        API-->>Br: click-to-load catalog
    end

    Note over Br,CO: Concierge tab (and Examples "Try it" → prefill)
    Br->>API: POST /api/concierge {text}  (?dry_run=1 if Preview toggle)
    API->>CO: run(...)  (see diagrams 02 / 03)
    CO-->>Br: reply (or dry-run plan)
```

**Gated + additive:** the Studio nav + route only appear when `GET /api/events/status` is ok
(i.e. `EVENTS_ENABLED=1`). Flag off → vanilla CUGA, byte-for-byte unchanged. All reads are
scope-filtered, so the UI shows only the caller's own channels/integrations/flows.

**Verified by:** the four read endpoints return `200` with correct payloads via FastAPI
TestClient (`.venv-events`); offline core 14/14. The `.tsx` is a **pre-built** bundle — rebuild via
`scripts/frontend_build.sh` (pnpm) after any change and restart the server; the Studio serves at
**http://localhost:8100/studio** (see [../STUDIO_UI.md](../STUDIO_UI.md)).
