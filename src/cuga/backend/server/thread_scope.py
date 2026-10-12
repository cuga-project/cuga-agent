"""Opaque LangGraph thread keys scoped to trusted deployment and owner identity.

The v1 namespace intentionally never falls back to raw legacy checkpoint keys:
old paused runs cannot resume through server routes after this upgrade. Saved
conversation history remains accessible through its existing scoped APIs.
Keep tenant and service instance IDs stable across replicas and restarts, or
ownership records and checkpoints will resolve to a different deployment scope.

The conversation database creates runtime_thread_owners on the first run or upload. Do not
remove owner rows during history cleanup: checkpoints and resources can outlive
history. Read and control requests never reserve unused IDs. Event fires retain
owners deliberately: disable_history still saves stream events and does not disable
checkpointing. A TTL on owner rows alone would allow another caller to claim retained
resources. Full resource purge (including checkpoints, workspace, knowledge and history)
must precede owner deletion. Thread routes do not currently provide a full purge.
Service-instance cleanup is database-scoped and does not implement a coordinated
resource purge; surviving workspace resources must be deleted or isolated before reuse.
Roll out to all workers together; old workers do not enforce this table.
"""

import hashlib
import json

from cuga.config import get_service_instance_id, get_tenant_id


def checkpoint_thread_id(thread_id: str, user_id: str, agent_id: str) -> str:
    identity = [get_tenant_id(), get_service_instance_id(), user_id, agent_id, thread_id]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"cuga-thread-v1:{digest}"
