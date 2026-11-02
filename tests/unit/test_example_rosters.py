"""Validate the example roster files without external services."""

import glob
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_every_example_roster_is_well_formed():
    """Every roster under events/examples/rosters/ must load: a supervisor with a name and
    instructions, uniquely named agents with instructions, and only MCP servers that exist."""
    import yaml

    servers_file = os.path.join(
        ROOT, "src", "cuga", "backend", "tools_env", "registry", "config", "mcp_servers_cuga_apps.yaml"
    )
    with open(servers_file, encoding="utf-8") as fh:
        known_servers = set(yaml.safe_load(fh)["mcpServers"])
    problems = []
    for path in sorted(glob.glob(os.path.join(ROOT, "events", "examples", "rosters", "*.yaml"))):
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        sup = cfg.get("supervisor") or {}
        if not sup.get("name") or not sup.get("special_instructions"):
            problems.append(f"{name}: supervisor needs a name and special_instructions")
        agents = cfg.get("agents") or []
        if not agents:
            problems.append(f"{name}: roster has no agents")
        names = [a.get("name") for a in agents]
        if len(names) != len(set(names)):
            problems.append(f"{name}: two agents share a name")
        for agent in agents:
            if not agent.get("name") or not agent.get("special_instructions"):
                problems.append(f"{name}: every agent needs a name and special_instructions")
            for server in agent.get("mcp_servers") or []:
                server_name = server.get("name") if isinstance(server, dict) else server
                if server_name not in known_servers:
                    problems.append(f"{name}: {agent.get('name')} uses unknown MCP server {server_name!r}")
    assert not problems, "\n".join(problems)
