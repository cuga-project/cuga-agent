"""Feature flag for the manage-UI multi-agent registry."""


def is_agent_registry_enabled() -> bool:
    from cuga.config import settings

    return bool(getattr(getattr(settings, "supervisor", None), "registry_enabled", False))
