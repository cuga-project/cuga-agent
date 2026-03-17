import json
import os
from typing import Any

from loguru import logger


def _get_client():
    try:
        import boto3
    except ImportError:
        return None
    try:
        from cuga.config import settings

        sec = getattr(settings, "secrets", None)
        region = (
            getattr(sec, "aws_region", "") or os.environ.get("AWS_REGION", "us-east-1")
            if sec
            else os.environ.get("AWS_REGION", "us-east-1")
        )
        return boto3.client("secretsmanager", region_name=region)
    except Exception as e:
        logger.debug("AWS Secrets Manager client init failed: {}", e)
        return None


class AwsBackend:
    scheme = "aws"

    def __init__(self):
        self._client = None

    def _client_or_none(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    def available(self) -> bool:
        try:
            self._client_or_none()
            return self._client is not None
        except Exception:
            return False

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
        client = self._client_or_none()
        if not client:
            return None
        path_with_field = path.strip()
        key_field = field
        if "#" in path_with_field:
            path, key_field = path_with_field.rsplit("#", 1)
            path = path.strip()
            key_field = key_field.strip() or field
        else:
            path = path_with_field
        if not path:
            return None
        try:
            resp = client.get_secret_value(SecretId=path)
            raw = resp.get("SecretString")
            if raw is None:
                return None
            if key_field:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        return data.get(key_field)
                except json.JSONDecodeError:
                    pass
                return None
            return raw
        except Exception as e:
            logger.debug("AWS get_secret_value failed: {}", e)
            return None
