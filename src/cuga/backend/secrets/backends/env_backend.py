import os
from typing import Any


class EnvBackend:
    scheme = "env"

    def get(
        self,
        path: str,
        *,
        field: str | None = None,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        instance_id: str | None = None,
        **kwargs: Any,
    ) -> str | None:
        val = os.environ.get(path)
        return val if val else None
