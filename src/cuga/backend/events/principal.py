"""Principal — the isolation key that flows through the events layer.

A ``Principal`` = (tenant_id, instance_id, user_id). Everything the events layer creates —
worker agents, subscriptions, threads, and **Activepieces flows/connections** — is scoped to
a principal, so two tenants/users don't share state.

Mapping to CUGA: ``user_id`` = the authenticated ``sub`` (CUGA already isolates conversations
by user_id); ``tenant_id``/``instance_id`` = the deployment/claim (CUGA stores these on every
row but treats them as process-global today — this lets the events layer key on them properly).

AP isolation: each principal maps to its own **AP project** (``ap_project_name``); the AP engine
find-or-creates that project and puts the principal's flows + connections there.

Stdlib-only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    tenant_id: str = "default"
    instance_id: str = "default"
    user_id: str = "local"

    @property
    def scope(self) -> str:
        """Per-USER key — namespaces threads/memory, subscriptions, connections."""
        return f"{self.tenant_id}/{self.instance_id}/{self.user_id}"

    @property
    def agent_scope(self) -> str:
        """Per-TENANT key for AGENT DEFINITIONS. Agents are built by a builder and shared across
        the tenant's users (access gated per-agent); only run-state (threads/subs/connections) is
        per-user. So look agents up here, but isolate execution by ``scope``."""
        return f"{self.tenant_id}/{self.instance_id}"

    def ap_project_name(self, grain: str = "tenant") -> str | None:
        """The Activepieces project displayName for this principal at a given ISOLATION GRAIN.

        grain='shared' → None (use the default project; isolation via flow-name namespacing).
        grain='tenant' → one project per tenant (the recommended multi-tenant boundary).
        grain='user'   → one project per user (strict; user-owned private creds)."""
        if grain == "shared":
            return None
        key = self.scope if grain == "user" else f"{self.tenant_id}/{self.instance_id}"
        return "ea::" + re.sub(r"[^A-Za-z0-9_.-]", "-", key)

    def connection_external_id(self, app: str) -> str:
        """The AP connection externalId for THIS user's credential for ``app`` (gmail/box/…).
        Always per-user, so each user's OAuth token is a distinct connection regardless of
        which project it lands in."""
        return re.sub(r"[^A-Za-z0-9_:.-]", "-", f"ea::{self.tenant_id}::{self.user_id}::{app}")

    def thread(self, thread_id: str) -> str:
        """Namespace a thread id so per-thread memory can't cross principals."""
        return f"{self.scope}::{thread_id}"


DEFAULT = Principal()


def resolve(*, tenant_id: str | None = None, instance_id: str | None = None,
            user_id: str | None = None, headers=None) -> Principal:
    """Resolve a Principal from explicit args → request headers → env → defaults.

    Headers: ``X-Tenant-Id`` / ``X-Instance-Id`` / ``X-User-Id`` (handy for the standalone
    layer and for testing). In CUGA, pass ``user_id=current_user.sub`` (+ a tenant claim).
    Env fallbacks mirror CUGA's ``DYNACONF_SERVICE__TENANT_ID`` / ``__INSTANCE_ID``.
    """
    h = headers or {}

    def pick(val, header, *envs, default):
        if val:
            return val
        if h.get(header):
            return h.get(header)
        for e in envs:
            if os.environ.get(e):
                return os.environ[e]
        return default

    return Principal(
        tenant_id=pick(tenant_id, "X-Tenant-Id", "DYNACONF_SERVICE__TENANT_ID",
                       "EVENTS_TENANT_ID", default="default"),
        instance_id=pick(instance_id, "X-Instance-Id", "DYNACONF_SERVICE__INSTANCE_ID",
                         default="default"),
        user_id=pick(user_id, "X-User-Id", "EVENTS_USER_ID", default="local"),
    )


def channel_native_id(source) -> str | None:
    """Pull the channel-native id out of an envelope source. Inbound flows use a thread_id of
    ``gw:<channel>:<native_id>`` (build_inbound_flow); a payload ``from_id`` also works."""
    tid = getattr(source, "thread_id", "") or ""
    if tid.startswith("gw:"):
        parts = tid.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    return None


def resolve_channel(channel: str, native_id: str, identity_map, *,
                    tenant_id: str | None = None, instance_id: str | None = None) -> Principal | None:
    """Map a channel-native id → a Principal via the identity map (decision 0007). Returns None
    if the native id isn't linked to a user yet (→ caller prompts an account-link)."""
    if identity_map is None or not native_id:
        return None
    t = tenant_id or os.environ.get("DYNACONF_SERVICE__TENANT_ID") or os.environ.get(
        "EVENTS_TENANT_ID", "default")
    uid = identity_map.resolve(t, channel, native_id)
    if uid is None:
        return None
    return Principal(tenant_id=t,
                     instance_id=instance_id or os.environ.get("DYNACONF_SERVICE__INSTANCE_ID", "default"),
                     user_id=uid)
