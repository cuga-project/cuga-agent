"""
KogitoProxy - thin client over a Kogito/Quarkus process service's generated REST API.

Targets a Kogito process service built from a BPMN model, e.g.:

    cd <kogito-project> && mvn quarkus:dev      # Swagger at /q/swagger-ui

Quarkus defaults to port 8080, which is also where the Flowable demo container listens,
so the CUGA FLO Kogito project pins itself to 8081 instead (see its
application.properties). A 404 carrying a Tomcat error page means this proxy reached
Flowable rather than Kogito.

Kogito generates one REST resource per process id:

    POST   /{processId}               start an instance (body = process variables)
    GET    /{processId}               list running instances
    GET    /{processId}/{instanceId}  read a running instance's variables
    DELETE /{processId}/{instanceId}  abort an instance

Two differences from FlowableProxy that are structural, not oversights:

* **No ``deploy()``.** Kogito compiles BPMN into the service at build time. There is
  no runtime deployment endpoint, so a process must already be built into the running
  service. Starting/building that service is out of scope here, exactly as FlowConfig
  does not start Flowable.
* **No REST redirect method.** Flowable's ``_change_process_state`` / ``realize_skip_to``
  exist but are unused at runtime, because REST reads committed state and races the
  in-flight script task. Hook redirection happens in-process inside the Kogito service
  (the Task_DynamicSkip equivalent), so this proxy deliberately offers no such path.

Quick CLI (against a running Kogito service):

    python -m cuga.backend.server.kogito.kogito_proxy ping
    python -m cuga.backend.server.kogito.kogito_proxy start loan_approval

`start` / `run` drive Kogito directly, with no CUGA FLO bridge behind them, so a
CUGA-instrumented process fails at its first control point with "cugaMcpUrl process
variable is not set". That is expected — use `cuga start flow_agent_inline <app>` to
exercise those; the CLI here is for connectivity checks and uninstrumented processes.
    python -m cuga.backend.server.kogito.kogito_proxy instances loan_approval
    python -m cuga.backend.server.kogito.kogito_proxy run loan_approval
    python -m cuga.backend.server.kogito.kogito_proxy result loan_approval <instance_id>
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

import httpx
from dotenv import find_dotenv, load_dotenv
from loguru import logger

# Load .env so config (base URL) lives there, not in code.
load_dotenv(find_dotenv(usecwd=True))

# 8081, not Quarkus's default 8080 — that port belongs to the Flowable demo container.
# Real values belong in .env (KOGITO_* keys) or the app's workflow_engine.url.
_FALLBACK_BASE_URL = "http://localhost:8081"
_FALLBACK_TIMEOUT = 30.0


class KogitoError(RuntimeError):
    """Raised when the Kogito REST API returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class KogitoProxy:
    """
    Synchronous client for a Kogito/Quarkus process service.

    Example:
        proxy = KogitoProxy()
        instance = proxy.start_process("loan_approval", variables={"loan_amount": 1000})
        print(proxy.get_variables("loan_approval", instance["id"]))
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        # Explicit args win; otherwise read KOGITO_* from the environment/.env.
        base_url = base_url or os.getenv("KOGITO_BASE_URL", _FALLBACK_BASE_URL)
        timeout = timeout if timeout is not None else float(os.getenv("KOGITO_TIMEOUT", _FALLBACK_TIMEOUT))

        self.base_url = base_url.rstrip("/")
        # ponytail: no auth — Kogito dev mode is open. Pass an `auth=` httpx kwarg here
        # if the target deployment sits behind Keycloak.
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    # --- context manager / lifecycle -------------------------------------

    def __enter__(self) -> "KogitoProxy":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- low-level helper -------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("/") else f"/{path}"
        logger.debug("Kogito {} {}", method, url)
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise KogitoError(f"Request to Kogito failed: {exc}") from exc

        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise KogitoError(
                f"Kogito {method} {url} -> {resp.status_code}: {body}",
                status_code=resp.status_code,
                body=body,
            )

        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # --- connectivity -----------------------------------------------------

    def ping(self) -> bool:
        """Return True if the Quarkus service is reachable."""
        try:
            self._request("GET", "/q/health/ready")
            return True
        except KogitoError as exc:
            logger.warning("Kogito ping failed: {}", exc)
            return False

    # --- process instances ------------------------------------------------

    def start_process(
        self,
        process_id: str,
        variables: Optional[dict[str, Any]] = None,
        business_key: Optional[str] = None,
    ) -> dict:
        """
        Start an instance of ``process_id``.

        Unlike Flowable, Kogito takes process variables as a plain JSON body — the
        process model *is* the variable set, so no {name, value, type} wrapping.

        For a fully-automated process (script/service tasks only) Kogito runs it to
        completion inside this call and the response body holds the terminal variables.
        """
        params = {"businessKey": business_key} if business_key else None
        result = self._request("POST", f"/{process_id}", json=variables or {}, params=params)
        logger.info("Started process '{}' instance id={}", process_id, (result or {}).get("id"))
        return result or {}

    def list_process_instances(self, process_id: str) -> list[dict]:
        """List currently-running instances of a process. Completed ones are not returned."""
        return self._request("GET", f"/{process_id}") or []

    def get_process_instance(self, process_id: str, instance_id: str) -> dict:
        """Read a running instance. Raises KogitoError(404) once the instance has ended."""
        return self._request("GET", f"/{process_id}/{instance_id}") or {}

    def delete_process_instance(self, process_id: str, instance_id: str) -> None:
        self._request("DELETE", f"/{process_id}/{instance_id}")

    def get_variables(self, process_id: str, instance_id: str) -> dict[str, Any]:
        """
        Return variables of a still-running instance.

        Kogito returns the process model itself, so this is the instance payload minus
        its ``id``. There is no history endpoint in a bare Kogito service — once the
        instance ends, this raises KogitoError(404) and the variables are only
        retrievable via Data Index, if that addon is deployed.
        """
        data = self.get_process_instance(process_id, instance_id)
        return {k: v for k, v in data.items() if k != "id"}

    # --- high-level services ---------------------------------------------

    def invoke_workflow(
        self,
        process_id: str,
        variables: Optional[dict[str, Any]] = None,
        *,
        business_key: Optional[str] = None,
        poll_interval: float = 1.0,
        timeout: float = 120.0,
    ) -> dict:
        """
        CLI convenience: start a process and wait for it to finish.

        Not on the CUGA runtime path — FlowAgent learns of completion through the
        ``complete_process`` MCP callback, not by polling.

        Automated processes end inside :meth:`start_process`, so their terminal
        variables come straight back. Processes that reach a user task keep running;
        those are polled until the instance disappears (Kogito 404s a finished
        instance) or ``timeout`` elapses.
        """
        started = self.start_process(process_id, variables=variables, business_key=business_key)
        instance_id = started.get("id")

        # Fast path: no id, or the instance is already gone — a fully-automated
        # process completed inside the start call and `started` holds the outputs.
        if not instance_id or not self._instance_alive(process_id, instance_id):
            logger.info("Workflow '{}' completed immediately (instance={})", process_id, instance_id)
            return {
                "instance_id": instance_id,
                "completed": True,
                "variables": {k: v for k, v in started.items() if k != "id"},
            }

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._instance_alive(process_id, instance_id):
                logger.info("Workflow '{}' completed (instance={})", process_id, instance_id)
                return {"instance_id": instance_id, "completed": True, "variables": {}}
            time.sleep(poll_interval)

        logger.warning(
            "Workflow '{}' still running after {}s (instance={})", process_id, timeout, instance_id
        )
        return {"instance_id": instance_id, "completed": False, "variables": {}}

    def _instance_alive(self, process_id: str, instance_id: str) -> bool:
        """True while the instance is still running; Kogito 404s it once it ends."""
        try:
            self.get_process_instance(process_id, instance_id)
            return True
        except KogitoError as exc:
            if exc.status_code == 404:
                return False
            raise

    def fetch_result(self, process_id: str, instance_id: str) -> dict:
        """
        CLI convenience: read a workflow instance's current result.

        Only works while the instance is alive. A bare Kogito service keeps no history,
        so a completed instance reports ``completed=True`` with empty variables — read
        the terminal variables from :meth:`invoke_workflow`'s return value instead, or
        deploy the Data Index addon.
        """
        try:
            variables = self.get_variables(process_id, instance_id)
        except KogitoError as exc:
            if exc.status_code != 404:
                raise
            return {"instance_id": instance_id, "completed": True, "variables": {}}
        return {"instance_id": instance_id, "completed": False, "variables": variables}

    # --- user tasks -------------------------------------------------------

    def complete_task(
        self,
        process_id: str,
        instance_id: str,
        task_name: str,
        task_instance_id: str,
        variables: Optional[dict[str, Any]] = None,
        user: Optional[str] = None,
    ) -> dict:
        """Complete a user task on a running instance via its generated endpoint."""
        params = {"user": user} if user else None
        result = self._request(
            "POST",
            f"/{process_id}/{instance_id}/{task_name}/{task_instance_id}",
            json=variables or {},
            params=params,
        )
        logger.info("Completed task '{}' on instance {}", task_name, instance_id)
        return result or {}


# --------------------------------------------------------------------------
# Minimal CLI for manual testing against a running Kogito service.
# --------------------------------------------------------------------------


def _print(obj: Any) -> None:
    import json

    print(json.dumps(obj, indent=2, default=str))


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0

    cmd, *rest = argv
    proxy = KogitoProxy()

    if cmd == "ping":
        ok = proxy.ping()
        print("reachable" if ok else "unreachable")
        return 0 if ok else 1

    if cmd == "start":
        if not rest:
            print("usage: start <process_id>")
            return 2
        _print(proxy.start_process(rest[0]))
        return 0

    if cmd == "instances":
        if not rest:
            print("usage: instances <process_id>")
            return 2
        _print(proxy.list_process_instances(rest[0]))
        return 0

    if cmd == "variables":
        if len(rest) < 2:
            print("usage: variables <process_id> <instance_id>")
            return 2
        _print(proxy.get_variables(rest[0], rest[1]))
        return 0

    if cmd == "run":
        if not rest:
            print("usage: run <process_id>   # invoke + wait for completion")
            return 2
        _print(proxy.invoke_workflow(rest[0]))
        return 0

    if cmd == "result":
        if len(rest) < 2:
            print("usage: result <process_id> <instance_id>")
            return 2
        _print(proxy.fetch_result(rest[0], rest[1]))
        return 0

    print(f"unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
