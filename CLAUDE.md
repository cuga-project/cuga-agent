# Git

Always include `[skip ci]` in every git commit message.

# Environment

If `cuga start` is not recognized, remind the user to run `source .venv/bin/activate` first.

# Running a Kogito app

Apps using `workflow_engine: {type: kogito}` need their Quarkus service built and running
before CUGA starts. Three steps, substituting the app directory name under
`docs/examples/flow_agent_app_inline/`:

```bash
# 1. Build the Kogito service from the app dir (one-off, or after editing the BPMN)
./scripts/build_kogito_app.sh <app-name>

# 2. Start it — leave this running in its own terminal
build/kogito/<app-name>/run.sh

# 3. In another terminal, start CUGA
source .venv/bin/activate
cuga start flow_agent_inline <app-name>
```

Then open http://127.0.0.1:8001 and give it an applicant.

Only step 1 needs repeating after a BPMN change — the yaml and policies are read live.
`loan_approval_kogito` is the worked example. See
`src/cuga/backend/cuga_graph/nodes/cuga_flow/README-KOGITO.md` for the full integration.

A Tomcat-styled 404 means the Kogito service is not up and the request reached Flowable on
8080 instead.
