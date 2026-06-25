#!/usr/bin/env bash

# Display usage information if help is requested
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Test runner script:"
    echo "  (no args)    Run default tests (registry + e2e + load + stability tests)"
    echo "  unit_tests         Run unit tests only (registry + agent-core + cuga-lite + supervisor + knowledge)"
    echo "  --skip-stability   Run all tests except stability tests"
    echo "  --help, -h   Show this help message"
    echo ""
    exit 0
fi

SKIP_STABILITY=
for arg in "$@"; do
    if [ "$arg" = "--skip-stability" ]; then
        SKIP_STABILITY=1
        break
    fi
done

echo "Starting tests with uv..."

echo "Running ruff check..."
if ! uv run ruff check; then
    echo "❌ Ruff check failed!"
    exit 1
fi

echo "Running ruff format..."
if ! uv run ruff format --check; then
    echo "❌ Ruff format check failed!"
    exit 1
fi

# Copy huggingface examples to cuga_workspace
echo "Copying huggingface examples to cuga_workspace..."
mkdir -p ./cuga_workspace
cp -r src/cuga/demo_tools/huggingface/* ./cuga_workspace/

# Helper function to run pytest and exit on failure
# Exit codes: 0=success, 1-4=failures, 5=no tests collected (treat as success)
run_pytest() {
    uv run pytest "$@" -v
    local ec=$?
    echo "pytest $* exited with code $ec"
    # Exit code 5 means no tests collected, which is not a failure
    if [ $ec -ne 0 ] && [ $ec -ne 5 ]; then
        echo "❌ Test failed! Exiting..."
        exit 1
    fi
}

# Helper function to run pytest with e2b dependencies installed
# Exit codes: 0=success, 1-4=failures, 5=no tests collected (treat as success)
run_pytest_with_e2b() {
    uv sync --extra e2b
    uv run pytest "$@" -v
    local ec=$?
    echo "pytest (with e2b) $* exited with code $ec"
    # Exit code 5 means no tests collected, which is not a failure
    if [ $ec -ne 0 ] && [ $ec -ne 5 ]; then
        echo "❌ Test failed! Exiting..."
        exit 1
    fi
}

echo "Running unit tests (registry + variables manager + local sandbox + E2B lite)..."
run_pytest ./src/cuga/backend/tools_env/registry/tests/
run_pytest ./src/cuga/backend/tools_env/registry/mcp_manager/tests/
run_pytest ./src/cuga/backend/cuga_graph/nodes/api/variables_manager/tests/
run_pytest_with_e2b ./src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/tests/
echo "Running agent-core tests..."
run_pytest ./src/cuga/backend/cuga_graph/nodes/cuga_agent_core/tests/
echo "Running cuga-lite tests..."
run_pytest ./src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/
echo "Running supervisor tests..."
run_pytest ./src/cuga/backend/cuga_graph/nodes/cuga_supervisor/tests/
echo "Running knowledge tests..."
run_pytest \
    tests/unit/test_knowledge_engine.py \
    tests/unit/test_session_knowledge.py \
    tests/unit/test_knowledge_routes.py \
    tests/unit/test_cuga_lite_knowledge_scopes.py \
    tests/unit/test_chat_knowledge_mode.py \
    tests/unit/test_chat_agent_knowledge_toggle.py \
    tests/integration/test_knowledge_integration.py
echo "Running cuga_lite bind_tools tests..."
run_pytest tests/unit/test_cuga_lite_bind_tools.py
echo "Running ToolGuard provider tests..."
run_pytest tests/unit/test_toolguard_provider.py \
    tests/unit/test_policy_tool_guide_loading.py \
    tests/unit/test_async_tool_provider_construction.py
echo "Running A2A tests..."
run_pytest tests/integration/a2a/ \
    tests/unit/test_a2a_simple_runner_hitl.py
echo "Running slash-command tests..."
run_pytest \
    tests/unit/test_slash_parser.py \
    tests/unit/test_slash_builtin_discovery.py \
    tests/unit/test_slash_message_synthesis.py \
    tests/unit/test_slash_arg_substitution.py \
    tests/unit/test_slash_command_resolver.py \
    tests/unit/test_slash_clear_builtin.py \
    tests/unit/test_slash_skills_builtin.py \
    tests/unit/test_slash_allowed_tools_enforcement.py \
    tests/unit/test_slash_langfuse_span.py \
    tests/unit/test_agent_state_message_rehydration.py \
    tests/unit/test_stream_event_format.py
echo "✅ All unit tests passed!"

# Check for test type flag
if [ "$1" = "unit_tests" ]; then
    exit 0
else
    echo "Running policy integration tests..."
    run_pytest ./src/cuga/backend/cuga_graph/policy/tests
    echo "Running SDK integration tests..."
    run_pytest ./src/cuga/sdk_core/tests/
    echo "Running manager API integration tests..."
    run_pytest ./tests/system/test_manager_api_integration.py
    echo "Running load test helpers..."
    run_pytest ./src/system_tests/load/tests/
    run_pytest tests/unit/test_load_test_mock_llm.py
    echo "Running mocked concurrent load test..."
    run_pytest ./src/system_tests/load/load_test_with_mocked_llm.py --load-test-users 5
    if [ -n "$SKIP_STABILITY" ]; then
        echo "Skipping stability tests (--skip-stability)"
        echo "✅ All tests passed!"
        exit 0
    fi
    echo "Running stability tests..."
    # Force unbuffered output for Python to ensure all logs are captured
    PYTHONUNBUFFERED=1 uv run run_stability_tests.py --method local
    ec=$?
    echo "stability tests exited with code $ec"
    if [ $ec -ne 0 ]; then
        echo "❌ Stability tests failed! Exiting..."
        exit 1
    fi
    echo "✅ All tests passed!"
    exit 0
fi
