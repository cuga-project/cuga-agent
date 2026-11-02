"""Keep the demo's default agent aligned with an explicitly selected registry file."""

import os


def custom_registry_tools() -> list[dict] | None:
    path = os.environ.get("MCP_SERVERS_FILE", "").strip()
    if not path or path.lower() == "none":
        # A roster supplies its own specialists. Do not add the unrelated
        # Digital Sales demo just because no custom registry file was supplied.
        return [] if os.environ.get("CUGA_SUPERVISOR_ROSTER", "").strip() else None
    from cuga.backend.tools_env.registry.config.config_loader import load_service_configs

    services = load_service_configs(path)
    return [
        {"name": name, **service.model_dump(mode="json", exclude_none=True, exclude={"name"})}
        for name, service in services.items()
    ]
