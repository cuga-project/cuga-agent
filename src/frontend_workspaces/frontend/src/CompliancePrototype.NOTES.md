# Compliance prototype

Question: How should CUGA expose a user's memories and an administrator's
compliance automations without presenting retention as a manual workflow?

Current hypothesis: Memory belongs beside Conversations, Agent Config, and
Manage. It opens on the current user's searchable memory inventory. Authorized
administrators can enter an Automation workspace that summarizes scheduled
retention and hook health, then inspect referenced memory activity and delivered
events through the same list-and-details interaction.

The implementation reads memory inventory and compliance status through CUGA's
Evolve integration. Retention previews invoke the real Evolve retention engine.
If Evolve is unavailable, the UI reports that state and does not substitute
fixture memories, activity, or deliveries. CUGA durably records simulated
scheduled runs, outbox events, and delivery attempts; automatic triggering and a
real delivery transport remain dependent on the eventing integration.

## Stakeholder exercise

1. Open Memory beside Conversations for the active agent.
2. As an administrator, choose **Load demonstration data**. This writes eight
   historical conversations through `ConversationHistoryDB` without executing
   an agent, then creates repairable, keyed memory records through MCP. The
   bootstrap response reports the actual Evolve readback and protection health.
3. Open Automation and run the simulated scheduled retention job. The
   retention decision itself comes from Evolve; CUGA persists the run, linked
   outbox events, and clearly labelled simulated deliveries. Payloads contain
   IDs and policy outcomes only, never memory content.
4. Select an affected memory or delivery to inspect its run/entity/thread IDs,
   then open the source conversation in the normal read-only chat view.

Automation configuration is stored per tenant, service instance, and active
agent. Retention frequency/time and the simulated event destination survive a
reload; the scheduler and delivery transport remain intentionally simulated.

## Local runtime

Start Evolve with a persistent demo directory and the PoC hook set:

```sh
EVOLVE_DATA_DIR=/tmp/cuga-compliance-evolve-data \
EVOLVE_HOOKS_CONFIG=examples/cuga_compliance_poc_hooks.yaml \
uv run evolve-mcp --transport sse --port 8201
```

Start CUGA with the full MCP integration:

```sh
DYNACONF_EVOLVE__ENABLED=true \
DYNACONF_EVOLVE__LITE_MODE_ONLY=false \
DYNACONF_EVOLVE__MODE=direct \
DYNACONF_EVOLVE__URL=http://127.0.0.1:8201/sse \
CUGA_COMPLIANCE_POC_SEED_ENABLED=1 \
uv run python -m uvicorn cuga.backend.server.main:app \
  --host 127.0.0.1 --port 7860
```

Provision the isolated demo store once with
`POST /api/admin/memory/poc/bootstrap`. The Memory page only reads live data
and never provisions or restores records. Remove
`CUGA_COMPLIANCE_POC_SEED_ENABLED` after setup to disable the provisioning
route.

The frontend opens the prototype from the existing **Memory** navigation item
at `/chat`.
