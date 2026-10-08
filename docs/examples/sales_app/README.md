# Harborline sales desk

Embedded CUGA SDK demo. The FastAPI app serves the sales UI and keeps a `CugaAgent` in the same process. The agent’s tools read and update the local sales book (accounts, contacts, leads, opportunities).

From the repository root, with the provider settings already in `.env`:

```bash
uv run python docs/examples/sales_app/app.py
```

Open http://127.0.0.1:8765. The pipeline, accounts, leads, and contacts load from the app. The assistant answers through `CugaAgent.invoke` and can move a deal with `update_opportunity_stage`.

This does not publish configuration to `cuga start manager`.
