import httpx

from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url


async def call_authenticate_apps(apps: list[str]) -> None:
    payload = {"apps": apps}
    async with httpx.AsyncClient() as client:
        registry_base = get_registry_base_url()
        response = await client.post(
            f"{registry_base}/api/authenticate_apps",
            json=payload,
        )
        response.raise_for_status()
