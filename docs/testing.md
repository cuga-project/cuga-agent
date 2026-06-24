# Testing

[← Back to README](../README.md)

## Test Scenarios - E2E

All tests are available through `./src/scripts/run_tests.sh`:

**Unit Tests**
- Registry: OpenAPI integration, MCP server functionality, service configurations
- Variables Manager: Core functionality, metadata handling, singleton pattern
- Code Executors: Local sandbox and E2B lite execution

**Policy Integration Tests** (`src/cuga/backend/cuga_graph/policy/tests/`)
- Intent Guard: Blocking behavior, priority resolution, multiple guard scenarios
- Playbook: Guidance injection, plan refinement, workflow execution
- Tool Approval: Human-in-the-loop approval flows (approve/deny)
- Tool Guide: Context enhancement and metadata injection
- Output Formatter: Response formatting and routing
- NL Trigger Conflict Resolution: Embedding-based similarity search with LLM conflict resolution
- Embedding Similarity: Vector search, policy matching, threshold validation
- Keyword Operators: AND/OR logic, case sensitivity, multi-keyword matching

**SDK Integration Tests** (`src/cuga/sdk_core/tests/`)
- SDK functionality: Agent invocation, streaming, tool integration
- Policy management: Policy loading, matching, and execution via SDK

**Stability Tests** (`run_stability_tests.py`)
- Fast Mode: Get top account by revenue, list accounts, find VP sales high-value accounts
- CRM Workflows: Contacts management, email operations, tool discovery
- HF Utterances: Account queries, revenue calculations, playbook execution
- Execution: Supports local and Docker execution, parallel/sequential modes, cross-version testing

## Running Tests

Run all tests (unit, integration, and stability):

```bash
./src/scripts/run_tests.sh
```

Run unit tests only:

```bash
./src/scripts/run_tests.sh unit_tests
```
