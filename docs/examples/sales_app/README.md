# Harborline sales desk

Embedded CUGA SDK demo. The FastAPI app serves the sales UI and keeps a `CugaAgent` in the same process. The agent’s tools read and update the local sales book (accounts, contacts, leads, opportunities).

From the repository root, with the provider settings already in `.env`:

```bash
uv run python docs/examples/sales_app/app.py
```

Open http://127.0.0.1:8765. The pipeline, accounts, leads, and contacts load from the app. The assistant answers through `CugaAgent.invoke` and can move a deal with `update_opportunity_stage`.

This does not publish configuration to `cuga start manager`.

## Managed server

`openapi_server.py` exposes the same book as OpenAPI for the managed server, which cannot take the in-process LangChain tools:

```bash
uv run python docs/examples/sales_app/openapi_server.py
```

That listens on http://127.0.0.1:8766. Point a managed tool at `http://127.0.0.1:8766/openapi.json` (`type: openapi`, name `harborline`), publish the agent, then use chat at http://127.0.0.1:7860/chat or `POST /run`.
