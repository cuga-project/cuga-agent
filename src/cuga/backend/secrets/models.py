from dataclasses import dataclass


@dataclass
class SecretRef:
    scheme: str
    path: str
    field: str | None = None
