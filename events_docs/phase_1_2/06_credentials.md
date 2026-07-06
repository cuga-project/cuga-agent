# 06 — Credentials: shared (service account) vs per-user

Each integration on an agent declares a **`credential_ownership`**. The events layer resolves it
to a concrete AP **connection externalId** at runtime.
[`credentials.py`](../../src/cuga/backend/events/credentials.py) `connection_external_id`;
AP CRUD in [`ap_engine.py`](../../src/cuga/backend/events/ap_engine.py). Decision:
[0003](../decisions/0003-credentials-ownership.md). AP owns + refreshes the creds (§10).

```mermaid
sequenceDiagram
    autonumber
    participant U as Chatting user
    participant CO as Concierge / worker
    participant CR as credentials.resolve
    participant AP as Activepieces connections

    U->>CO: use an integration (e.g. send Telegram / read Gmail)
    CO->>CR: connection_external_id(app, ownership, principal, agent)

    alt ownership = shared (service account)
        CR-->>CO: ea::<tenant>::agent::<agent>::<app>
        Note over CO,AP: one connection · every user shares it<br/>(builder authorized it once)
    else ownership = per-user
        CR-->>CO: ea::<tenant>::<user>::<app>
        CO->>AP: connection_exists(externalId)?
        alt connected
            AP-->>CO: yes → proceed
        else not connected
            AP-->>CO: no
            CO-->>U: "connect your <app>" + AP connect URL (just-in-time)
        end
    end
```

**OAuth vs token:** OAuth apps (Gmail/Box) are authorized in AP's connect UI (can't be minted
headlessly); token apps (GitHub PAT, Telegram) can be created via the API
(`ensure_secret_connection`, SECRET_TEXT).

**Verified by:** `live_credentials_check.py` — `shared` → alice & bob resolve to the **same**
connection; `per-user` → **distinct** (`…::alice::…` ≠ `…::bob::…`); an unconnected user →
**needs a connect**. The Studio **Integrations** tab renders this status (diagram 07).
