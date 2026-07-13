"""
FlowableProxy - thin client over the Flowable UI process-engine REST API.

Targets the all-in-one Flowable UI image:

    docker pull flowable/flowable-ui:latest
    docker run -p 8080:8080 flowable/flowable-ui:latest

In that image the BPMN process-engine REST API is served under
``/flowable-ui/process-api`` with HTTP basic auth (default ``admin`` / ``test``).

This proxy lets CUGA upload (deploy) BPMN process models, start process
instances, inspect definitions/instances/variables, and complete user tasks
- the operations needed to drive Flowable from the agent side.

Quick CLI (after the container is up):

    # deploy a model
    python -m cuga.backend.server.flowable.flowable_proxy deploy \
        ~/Downloads/Loan-Approval-Process.bpmn20.xml

    # list deployed process definitions
    python -m cuga.backend.server.flowable.flowable_proxy definitions

    # start an instance by definition key (key == the BPMN <process id>)
    python -m cuga.backend.server.flowable.flowable_proxy start Process_1s3q83l

    # service 1: invoke a workflow and wait until Flowable finishes it
    python -m cuga.backend.server.flowable.flowable_proxy run Process_1s3q83l

    # service 2: fetch the result of a completed instance
    python -m cuga.backend.server.flowable.flowable_proxy result <instance_id>

    # end-to-end demo against the Loan-Approval-Process model
    python -m cuga.backend.server.flowable.flowable_proxy demo \
        ~/Downloads/Loan-Approval-Process.bpmn20.xml
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import find_dotenv, load_dotenv
from loguru import logger

# Load .env so config (base URL / credentials) lives there, not in code.
load_dotenv(find_dotenv(usecwd=True))

# Fallbacks match the flowable/flowable-ui:latest container; real values
# belong in .env (see FLOWABLE_* keys in .env.example).
_FALLBACK_BASE_URL = "http://localhost:8080/flowable-ui/process-api"
_FALLBACK_USER = "admin"
_FALLBACK_PASSWORD = "test"
_FALLBACK_TIMEOUT = 30.0


class FlowableError(RuntimeError):
    """Raised when the Flowable REST API returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class FlowableProxy:
    """
    Synchronous client for the Flowable process-engine REST API.

    Example:
        proxy = FlowableProxy()
        proxy.deploy("Loan-Approval-Process.bpmn20.xml")
        instance = proxy.start_process("Process_1s3q83l",
                                       variables={"amount": 1000})
        print(proxy.get_variables(instance["id"]))
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        # Explicit args win; otherwise read FLOWABLE_* from the environment/.env.
        base_url = base_url or os.getenv("FLOWABLE_BASE_URL", _FALLBACK_BASE_URL)
        username = username or os.getenv("FLOWABLE_USER", _FALLBACK_USER)
        password = password or os.getenv("FLOWABLE_PASSWORD", _FALLBACK_PASSWORD)
        timeout = timeout if timeout is not None else float(os.getenv("FLOWABLE_TIMEOUT", _FALLBACK_TIMEOUT))

        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=(username, password),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    # --- context manager / lifecycle -------------------------------------

    def __enter__(self) -> "FlowableProxy":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- low-level helper -------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("/") else f"/{path}"
        logger.debug("Flowable {} {}", method, url)
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise FlowableError(f"Request to Flowable failed: {exc}") from exc

        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise FlowableError(
                f"Flowable {method} {url} -> {resp.status_code}: {body}",
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
        """Return True if the REST API is reachable and auth succeeds."""
        try:
            self._request("GET", "/repository/process-definitions", params={"size": 1})
            return True
        except FlowableError as exc:
            logger.warning("Flowable ping failed: {}", exc)
            return False

    # --- deployments (upload process models) ------------------------------

    def deploy(self, bpmn_path: str | Path, deployment_name: Optional[str] = None) -> dict:
        """
        Upload (deploy) a BPMN 2.0 model file. The filename MUST end in
        ``.bpmn`` or ``.bpmn20.xml`` for Flowable to parse it as a process.

        Returns the created deployment resource.
        """
        path = Path(bpmn_path).expanduser()
        if not path.is_file():
            raise FlowableError(f"BPMN file not found: {path}")

        name = deployment_name or path.name
        # Flowable keys off the upload filename's extension; keep .bpmn20.xml.
        upload_name = path.name
        if not (upload_name.endswith(".bpmn") or upload_name.endswith(".bpmn20.xml")):
            upload_name = f"{path.stem}.bpmn20.xml"

        files = {"file": (upload_name, path.read_bytes(), "text/xml")}
        result = self._request(
            "POST",
            "/repository/deployments",
            files=files,
            params={"deploymentName": name},
        )
        logger.info("Deployed '{}' as deployment id={}", name, result.get("id"))
        return result

    def list_deployments(self) -> list[dict]:
        return self._request("GET", "/repository/deployments").get("data", [])

    def delete_deployment(self, deployment_id: str, cascade: bool = True) -> None:
        """Delete a deployment (cascade removes its running instances too)."""
        self._request(
            "DELETE",
            f"/repository/deployments/{deployment_id}",
            params={"cascade": str(cascade).lower()},
        )
        logger.info("Deleted deployment id={}", deployment_id)

    # --- process definitions ---------------------------------------------

    def list_process_definitions(self, latest: bool = True) -> list[dict]:
        params = {"latest": str(latest).lower(), "size": 100}
        return self._request("GET", "/repository/process-definitions", params=params).get("data", [])

    def get_process_definition(self, definition_id: str) -> dict:
        return self._request("GET", f"/repository/process-definitions/{definition_id}")

    # --- runtime: start / inspect instances -------------------------------

    def start_process(
        self,
        process_key: str,
        variables: Optional[dict[str, Any]] = None,
        business_key: Optional[str] = None,
    ) -> dict:
        """
        Start a process instance by its definition key (the BPMN ``<process id>``).

        ``variables`` is a plain dict; it is converted to Flowable's
        ``[{name, value, type}]`` wire format automatically.
        """
        payload: dict[str, Any] = {"processDefinitionKey": process_key}
        if business_key:
            payload["businessKey"] = business_key
        if variables:
            payload["variables"] = [_to_flowable_var(k, v) for k, v in variables.items()]

        result = self._request("POST", "/runtime/process-instances", json=payload)
        logger.info("Started process '{}' instance id={}", process_key, result.get("id"))
        return result

    def list_process_instances(self) -> list[dict]:
        return self._request("GET", "/runtime/process-instances", params={"size": 100}).get("data", [])

    def get_process_instance(self, instance_id: str) -> dict:
        return self._request("GET", f"/runtime/process-instances/{instance_id}")

    def delete_process_instance(self, instance_id: str, reason: str = "deleted via proxy") -> None:
        self._request("DELETE", f"/runtime/process-instances/{instance_id}", params={"deleteReason": reason})

    def get_variables(self, instance_id: str) -> dict[str, Any]:
        """Return runtime variables of a (still-running) process instance."""
        data = self._request("GET", f"/runtime/process-instances/{instance_id}/variables")
        return {v["name"]: v.get("value") for v in (data or [])}

    def get_historic_variables(self, instance_id: str) -> dict[str, Any]:
        """Return variables from history (works after the instance has ended)."""
        params = {"processInstanceId": instance_id}
        data = self._request("GET", "/history/historic-variable-instances", params=params).get("data", [])
        return {v["variable"]["name"]: v["variable"].get("value") for v in data}

    def get_historic_process_instance(self, instance_id: str) -> dict:
        """Return the historic process-instance record (has ``endTime`` once done)."""
        return self._request("GET", f"/history/historic-process-instances/{instance_id}")

    # --- high-level services ---------------------------------------------

    def invoke_workflow(
        self,
        process_key: str,
        variables: Optional[dict[str, Any]] = None,
        *,
        business_key: Optional[str] = None,
        poll_interval: float = 1.0,
        timeout: float = 120.0,
    ) -> dict:
        """
        Service 1: invoke a workflow and let Flowable run it to completion.

        Starts an instance by definition key, then blocks until the process
        ends (or ``timeout`` elapses). Returns a small status dict; call
        :meth:`fetch_result` with the returned ``instance_id`` to read outputs.

        Fully-automated processes (script/service tasks) end during the start
        call. Processes that reach a user task will not complete on their own
        and return ``completed=False`` after the timeout.
        """
        started = self.start_process(process_key, variables=variables, business_key=business_key)
        instance_id = started["id"]

        # Fast path: the start response already reports the instance ended.
        if started.get("ended"):
            logger.info("Workflow '{}' completed immediately (instance={})", process_key, instance_id)
            return {"instance_id": instance_id, "completed": True}

        # Otherwise poll history until endTime is set or we time out.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            historic = self.get_historic_process_instance(instance_id)
            if historic.get("endTime"):
                logger.info("Workflow '{}' completed (instance={})", process_key, instance_id)
                return {"instance_id": instance_id, "completed": True}
            time.sleep(poll_interval)

        logger.warning(
            "Workflow '{}' still running after {}s (instance={})", process_key, timeout, instance_id
        )
        return {"instance_id": instance_id, "completed": False}

    def fetch_result(self, instance_id: str) -> dict:
        """
        Service 2: fetch the result of a (completed) workflow instance.

        Reads process variables from history, so it works after the instance
        has ended. ``completed`` reflects whether Flowable recorded an end time.
        """
        historic = self.get_historic_process_instance(instance_id)
        return {
            "instance_id": instance_id,
            "completed": bool(historic.get("endTime")),
            "process_definition": historic.get("processDefinitionName"),
            "start_time": historic.get("startTime"),
            "end_time": historic.get("endTime"),
            "variables": self.get_historic_variables(instance_id),
        }

    # --- user tasks -------------------------------------------------------

    def list_tasks(self, process_instance_id: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"size": 100}
        if process_instance_id:
            params["processInstanceId"] = process_instance_id
        return self._request("GET", "/runtime/tasks", params=params).get("data", [])

    def complete_task(self, task_id: str, variables: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {"action": "complete"}
        if variables:
            payload["variables"] = [_to_flowable_var(k, v) for k, v in variables.items()]
        self._request("POST", f"/runtime/tasks/{task_id}", json=payload)
        logger.info("Completed task id={}", task_id)

    # --- hook realisation ------------------------------------------------

    def realize_hook_action(
        self,
        result: Any,
        process_instance_id: str,
        current_activity_id: str | None = None,
    ) -> None:
        """Translate a HookResult into Flowable REST calls.

        CONTINUE requires no REST interaction — Flowable advances automatically
        once the script task HTTP response is received.

        SKIP_TO suspends the process instance first so no competing token can
        advance while the state change is applied, then resumes after.
        current_activity_id is used as a cancel-ID hint when the execution query
        returns an empty activityId list (which happens when Flowable's script task
        is mid-execution and hasn't flushed its activityId to DB yet).
        """
        from cuga.backend.cuga_graph.nodes.cuga_flow.hook_manager import HookAction

        action = result.action

        if action == HookAction.CONTINUE:
            logger.info(
                "Hook CONTINUE for instance {} — no REST action required, "
                "Flowable advances after script task response",
                process_instance_id,
            )

        elif action == HookAction.SKIP_TO:
            target = result.skip_to_node
            logger.info(
                "Hook SKIP_TO for instance {} to {} — routing via BPMN boundary event + Task_DynamicSkip",
                process_instance_id,
                target,
            )

        elif action == HookAction.TERMINATE:
            logger.info(
                "Hook TERMINATE for instance {} — routing via BPMN boundary event + Task_DynamicSkip to complete process task",
                process_instance_id,
            )

        else:
            logger.warning(
                "Hook action '{}' for instance {} is not yet implemented in FlowableProxy",
                action.value,
                process_instance_id,
            )

    def _set_suspension_state(self, process_instance_id: str, *, suspend: bool) -> None:
        """Suspend or resume a process instance."""
        action = "suspend" if suspend else "activate"
        self._request(
            "PUT",
            f"/runtime/process-instances/{process_instance_id}",
            json={"action": action},
        )
        logger.debug(
            "Process instance {} {}", process_instance_id, "suspended" if suspend else "activated"
        )

    def _change_process_state(
        self,
        process_instance_id: str,
        target_activity_id: str,
        hint_cancel_id: str | None = None,
    ) -> None:
        """Cancel all current active activities and start execution at target_activity_id.

        hint_cancel_id is used as a fallback when the executions query returns no
        activityId values — this happens when the hook script task is mid-execution
        and Flowable hasn't flushed its activityId to the DB yet.
        """
        data = self._request(
            "GET", "/runtime/executions",
            params={"processInstanceId": process_instance_id},
        )
        executions = (data or {}).get("data", [])
        cancel_ids = [e["activityId"] for e in executions if e.get("activityId")]
        if not cancel_ids and hint_cancel_id:
            cancel_ids = [hint_cancel_id]
        self._request(
            "POST",
            f"/runtime/process-instances/{process_instance_id}/change-state",
            json={"cancelActivityIds": cancel_ids, "startActivityIds": [target_activity_id]},
        )
        logger.info(
            "Changed state for instance {}: cancelled {} → started {}",
            process_instance_id, cancel_ids, target_activity_id,
        )

    def realize_skip_to(self, process_instance_id: str, target_activity_id: str) -> None:
        """Move execution to target_activity_id via change-state (called from Task_DynamicSkip)."""
        self._change_process_state(
            process_instance_id, target_activity_id, hint_cancel_id="Task_DynamicSkip"
        )

    def _set_process_variables(self, process_instance_id: str, variables: dict[str, Any]) -> None:
        """Set or update process variables by name before a state change."""
        for name, value in variables.items():
            self._request(
                "PUT",
                f"/runtime/process-instances/{process_instance_id}/variables/{name}",
                json=_to_flowable_var(name, value),
            )
            logger.debug("Set process variable '{}' on instance {}", name, process_instance_id)


def _to_flowable_var(name: str, value: Any) -> dict[str, Any]:
    """Map a Python value to Flowable's variable wire format."""
    if isinstance(value, bool):
        var_type = "boolean"
    elif isinstance(value, int):
        var_type = "integer"
    elif isinstance(value, float):
        var_type = "double"
    else:
        var_type = "string"
        value = value if isinstance(value, str) else str(value)
    return {"name": name, "value": value, "type": var_type}


# --------------------------------------------------------------------------
# Minimal CLI for manual testing against a running container.
# --------------------------------------------------------------------------


def _print(obj: Any) -> None:
    import json

    print(json.dumps(obj, indent=2, default=str))


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0

    cmd, *rest = argv
    proxy = FlowableProxy()

    if cmd == "ping":
        ok = proxy.ping()
        print("reachable" if ok else "unreachable")
        return 0 if ok else 1

    if cmd == "deploy":
        if not rest:
            print("usage: deploy <path-to.bpmn20.xml>")
            return 2
        _print(proxy.deploy(rest[0]))
        return 0

    if cmd == "deployments":
        _print(proxy.list_deployments())
        return 0

    if cmd == "definitions":
        _print(proxy.list_process_definitions())
        return 0

    if cmd == "start":
        if not rest:
            print("usage: start <process_definition_key>")
            return 2
        _print(proxy.start_process(rest[0]))
        return 0

    if cmd == "instances":
        _print(proxy.list_process_instances())
        return 0

    if cmd == "variables":
        if not rest:
            print("usage: variables <process_instance_id>")
            return 2
        _print(proxy.get_historic_variables(rest[0]))
        return 0

    if cmd == "run":
        if not rest:
            print("usage: run <process_definition_key>   # invoke + wait for completion")
            return 2
        _print(proxy.invoke_workflow(rest[0]))
        return 0

    if cmd == "result":
        if not rest:
            print("usage: result <process_instance_id>   # fetch workflow result")
            return 2
        _print(proxy.fetch_result(rest[0]))
        return 0

    if cmd == "demo":
        bpmn = rest[0] if rest else str(Path.home() / "Downloads" / "Loan-Approval-Process.bpmn20.xml")
        logger.info("Deploying {}", bpmn)
        proxy.deploy(bpmn)
        defs = proxy.list_process_definitions()
        logger.info("Process definitions: {}", [d["key"] for d in defs])
        if not defs:
            logger.error("No process definitions after deploy")
            return 1
        key = defs[0]["key"]
        instance = proxy.start_process(key)
        iid = instance["id"]
        # Loan-Approval-Process is fully automated (script tasks) and ends
        # immediately, so read the result from history.
        result_vars = proxy.get_historic_variables(iid)
        logger.info("Instance {} ended. Variables: {}", iid, result_vars)
        _print({"process_definition_key": key, "instance_id": iid, "variables": result_vars})
        return 0

    print(f"unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
