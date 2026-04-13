# CUGA Demo Setup

## Installation
```bash
uvx --from git+https://github.com/cuga-project/cuga-agent.git#subdirectory=docs/examples/demo_apps/setup create-cuga-demo [--cache] [--local]
```

## Local Development
```bash
uv run create-cuga-demo [--cache] [--local]
# or
CUGA_LOCAL=1 CUGA_SOURCE_DIR=/path/to/cuga-agent/src/cuga/demo_tools uvx --from git+https://github.com/cuga-project/cuga-agent.git#subdirectory=docs/examples/demo_apps/setup create-cuga-demo [--cache]
```
```bash
CUGA_LOCAL=1 CUGA_SOURCE_DIR=/Users/you/cuga-agent/src/cuga/demo_tools uvx --from /path/to/cuga-agent/docs/examples/demo_apps/setup create-cuga-demo
```

## Environment Variables
- `CUGA_LOCAL=1` - Use local demo apps instead of git installs
- `CUGA_SOURCE_DIR=/path/to/src/cuga/demo_tools` - Path to bundled demo MCP projects (when running from a uvx temp directory)

Demo app sources live in **`src/cuga/demo_tools/`** in the repository.
