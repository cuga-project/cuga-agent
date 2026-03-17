# System Tests for CUGA Manager API

This directory contains system-level integration tests for the CUGA Manager API.

## Overview

The system tests verify the complete workflow of the CUGA agent manager, including:

- Creating and managing agent configurations
- Draft vs production mode isolation
- Tool management and partial tool selection
- Version control for agent configurations
- API integration with the manager server

## Test File

### `test_manager_api_integration.py`

Comprehensive system tests that cover:

1. **Draft Configuration Management**
   - Saving draft configurations with tools
   - Retrieving draft configurations
   - Running tasks in draft mode

2. **Version Publishing**
   - Publishing draft as new version
   - Retrieving published configurations
   - Running tasks in production mode

3. **Isolation Testing**
   - Draft vs production tool isolation
   - Ensuring changes in draft don't affect production

4. **Tool Management**
   - Partial tool selection from connected apps
   - Tool include lists for fine-grained control

5. **Version History**
   - Configuration history tracking
   - Multiple version management

## Running the Tests

### Prerequisites

1. Ensure all dependencies are installed:
   ```bash
   pip install pytest httpx loguru
   ```

2. Make sure the CUGA CLI is available in your PATH

### Run All System Tests

```bash
# From the project root
pytest tests/system/test_manager_api_integration.py -v -s
```

### Run Specific Test

```bash
pytest tests/system/test_manager_api_integration.py::TestManagerAPIWorkflow::test_01_save_draft_config -v -s
```

### Run with Coverage

```bash
pytest tests/system/test_manager_api_integration.py --cov=src/cuga/backend/server --cov-report=html -v -s
```

## Test Setup

The tests automatically:

1. **Clean up database files** - Removes all `.db` files from `DBS_DIR` before starting
2. **Start the manager** - Launches `cuga start manager` with `CUGA_MANAGER_MODE=true`
3. **Wait for readiness** - Polls the health endpoint until the server is ready
4. **Run tests** - Executes all test cases in sequence
5. **Cleanup** - Stops the manager process after all tests complete

## Test Configuration

- **Manager URL**: `http://localhost:7860`
- **Registry URL**: `http://localhost:8001`
- **Test Agent ID**: `cuga-default`
- **Startup Timeout**: 60 seconds
- **Health Check Interval**: 1 second

## Test Fixtures

### `cleanup_and_start_manager`
Module-scoped fixture that handles database cleanup and manager lifecycle.

### `http_client`
Provides an HTTP client with 30-second timeout for API calls.

### `test_agent_config`
Provides a basic agent configuration with filesystem tool.

### `test_agent_config_with_partial_tools`
Provides an agent configuration with partial tool selection (include lists).

## Test Flow

The tests are numbered and should run in sequence:

1. `test_01_save_draft_config` - Save initial draft
2. `test_02_get_draft_config` - Verify draft retrieval
3. `test_03_run_task_in_draft_mode` - Execute task in draft
4. `test_04_publish_draft_as_version` - Publish as version 1
5. `test_05_get_published_config` - Verify published config
6. `test_06_run_task_in_production_mode` - Execute task in production
7. `test_07_draft_vs_production_isolation` - Verify isolation
8. `test_08_partial_tool_selection` - Test include lists
9. `test_09_config_history` - Verify version history
10. `test_10_multiple_versions` - Test multiple version management

## Expected Behavior

### Draft Mode
- Uses `X-Use-Draft: true` header
- Reads from draft configuration
- Changes don't affect production
- Tools are isolated to draft state

### Production Mode
- No `X-Use-Draft` header (or `false`)
- Reads from published version
- Stable and isolated from draft changes
- Uses published tool configuration

### Tool Isolation
- Draft can have different tools than production
- Tool include lists are respected
- Registry reloads on configuration changes

## Troubleshooting

### Manager Won't Start
- Check if ports 7860 and 8001 are available
- Verify CUGA CLI is installed correctly
- Check logs in `LOGGING_DIR`

### Tests Timeout
- Increase `MANAGER_STARTUP_TIMEOUT` if needed
- Check system resources
- Verify network connectivity to localhost

### Database Issues
- Ensure `DBS_DIR` is writable
- Check for file permission issues
- Verify SQLite is available

## Adding New Tests

When adding new tests:

1. Follow the naming convention: `test_XX_descriptive_name`
2. Add appropriate logging with `logger.info()`
3. Use assertions with descriptive messages
4. Clean up any test-specific resources
5. Update this README with test description

## CI/CD Integration

These tests can be integrated into CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run System Tests
  run: |
    pytest tests/system/test_manager_api_integration.py -v --junitxml=test-results.xml
```

## Notes

- Tests use a separate test agent ID to avoid conflicts
- Database is cleaned before each test run
- Manager process is automatically managed
- All HTTP requests have 30-second timeout
- Tests are designed to be idempotent