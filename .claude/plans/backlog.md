# CUGA FLO — TODO backlog

Unordered. Each is a heading of work, not a scoped plan yet.

- [ ] **Headless mode — CUGA FLO as a service.** Run without the web UI; drive flows over an
      API so another system can start a process, feed input, and read results.

- [ ] **Move CUGA FLO out of the branch into its own repo** under the cuga projects org,
      instead of living on the `cugaflo` branch of this repo.

- [ ] **Merge CUGA FLO Studio into CUGA FLO.** One codebase / one deployable instead of two.

- [ ] **Embed a process editor in the Studio** from Apache KIE (BPMN editor component).

- [ ] **Context / memory sharing across agents.** Let the wrapper agents and any remote
      agents see shared context rather than each starting cold.

- [ ] **Handle process variables more rigorously.** Tighter validation, typing, and
      lifecycle for BPMN process variables end to end (YAML ↔ BPMN ↔ engine).
