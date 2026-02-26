#!/usr/bin/env bash

# Display usage information if help is requested
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Test runner script:"
    echo "  (no args)    Run default tests (registry + e2e + memory + stability tests)"
    echo "  unit_tests         Run unit tests only (registry + variables manager + sandbox + E2B lite)"
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
cp -r docs/examples/huggingface/* ./cuga_workspace/

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

# Helper function to run pytest with memory dependencies installed
# Exit codes: 0=success, 1-4=failures, 5=no tests collected (treat as success)
run_pytest_with_memory() {
    # Sync memory dependency groups before running tests
    uv sync --extra memory
    uv run pytest "$@" -v
    local ec=$?
    echo "pytest (with memory) $* exited with code $ec"
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
run_pytest ./src/cuga/backend/cuga_graph/nodes/api/variables_manager/tests/
run_pytest_with_e2b ./src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/tests/
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
    echo "Running memory tests..."
    run_pytest_with_memory ./src/system_tests/unit/test_cli.py
    run_pytest_with_memory ./src/system_tests/e2e/test_memory_integration.py
    run_pytest_with_memory ./src/system_tests/e2e/balanced_test_memory.py
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