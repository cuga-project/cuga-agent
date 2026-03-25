from importlib import import_module
from importlib import util
from importlib.metadata import PackageNotFoundError, version

from cuga.backend.memory.memory import RunRecord, get_kaizen_client

_KAIZEN_EXPORTS = {
    "EntityUpdate": ("kaizen.schema.conflict_resolution", "EntityUpdate"),
    "Entity": ("kaizen.schema.core", "Entity"),
    "Namespace": ("kaizen.schema.core", "Namespace"),
    "RecordedEntity": ("kaizen.schema.core", "RecordedEntity"),
    "KaizenException": ("kaizen.schema.exceptions", "KaizenException"),
    "NamespaceAlreadyExistsException": ("kaizen.schema.exceptions", "NamespaceAlreadyExistsException"),
    "NamespaceNotFoundException": ("kaizen.schema.exceptions", "NamespaceNotFoundException"),
}


def __getattr__(name: str):
    if name not in _KAIZEN_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, symbol = _KAIZEN_EXPORTS[name]
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if util.find_spec("kaizen") is None:
            raise RuntimeError(
                "Kaizen is required for memory features but is not installed. "
                "Install with `uv sync --extra memory` (or `pip install \"cuga[memory]\"`) and rerun."
            ) from exc
        try:
            kaizen_version = version("kaizen")
        except PackageNotFoundError:
            kaizen_version = "unknown"
        raise RuntimeError(
            "Kaizen is installed but incompatible with CUGA memory integration "
            f"(installed version: {kaizen_version}). "
            "Expected modules such as `kaizen.config`, `kaizen.frontend`, and `kaizen.schema` "
            "were not found."
        ) from exc

    value = getattr(module, symbol)
    globals()[name] = value
    return value


__all__ = [
    "get_kaizen_client",
    "RunRecord",
    "Entity",
    "RecordedEntity",
    "EntityUpdate",
    "Namespace",
    "KaizenException",
    "NamespaceNotFoundException",
    "NamespaceAlreadyExistsException",
]
