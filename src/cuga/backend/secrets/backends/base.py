from typing import Any, Protocol


class AbstractSecretBackend(Protocol):
    scheme: str

    def get(
        self,
        path: str,
        *,
        field: str | None = None,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        instance_id: str | None = None,
        **kwargs: Any,
    ) -> str | None: ...
