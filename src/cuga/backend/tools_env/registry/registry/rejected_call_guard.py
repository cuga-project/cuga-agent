"""Deterministic guard against re-issuing an identical rejected API call (#599).

The registry already returns well-formed, actionable 4xx errors, but nothing
carries state between turns about which call signatures have been definitively
rejected — so an agent can re-send the same call with the same arguments across
dozens of turns (observed: 2,136 identical rejections in one task). This guard
lives at the ``/functions/call`` choke point, which every execution path shares
(the local ``call_api`` helper and the remote-sandbox injected helper both POST
there), and escalates in two tiers:

1. **Escalate** — once a signature has been rejected ``rejected_call_escalate_after``
   times, further rejections of the same signature get a prominent prefix telling
   the model this exact call already failed identically and must be changed.
2. **Short-circuit** — once it has been rejected ``rejected_call_block_after``
   times, further identical calls are refused *without reaching the API*, with
   the stored error and an explicit directive. The refusal reuses the standard
   ``{"status": "exception", ...}`` shape, so clients handle it like any other
   rejection.

Only *definitive* rejections count (400/402/404/405/409/410/422). Auth and
transient statuses (401/403/408/429) are excluded: those can start succeeding
after out-of-band state changes (token refresh, rate-limit reset) without the
arguments changing.

A successful **mutating** call (any method but GET/HEAD, to any app) clears all
counters: state has changed, so previously-failing calls may now legitimately
succeed with identical arguments — e.g. a Venmo transaction rejected for
insufficient balance becomes valid after a top-up. Clearing is deliberately
global, not per-app, because the precondition fix often lives in a different
app than the failing call.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

# Statuses that mark a call signature as definitively rejected: re-sending the
# identical request cannot succeed unless server-side state changes first.
GUARDED_STATUS_CODES = frozenset({400, 402, 404, 405, 409, 410, 422})

# Argument keys excluded from the signature: they can rotate between attempts
# (token refresh) without making the call logically different.
_SIGNATURE_IGNORED_KEYS = frozenset({"access_token"})


@dataclass
class _Rejection:
    count: int
    status_code: int
    message: str
    # How the rejection was served to the client: True = HTTP 4xx JSONResponse
    # (registry-raised errors), False = HTTP 200 with an exception-shaped dict
    # body (AppWorld adapter errors, which arrive wrapped in TextContent). A
    # short-circuit must mirror the recorded flavor so generated code sees the
    # refusal exactly the way it saw the original rejection.
    served_as_http_error: bool = True


class RejectedCallGuard:
    """Tracks per-signature rejection counts and decides escalate/short-circuit.

    Thresholds are read from settings on every call so tests and runtime config
    changes take effect immediately; 0 disables the corresponding tier.
    """

    def __init__(self) -> None:
        self._rejections: Dict[str, _Rejection] = {}
        self._lock = threading.Lock()

    # ── Configuration ──────────────────────────────────────────────────────
    #
    # Thresholds are normalized, not rejected: advanced_features has no schema
    # validation layer, and a bad value must degrade safely, never block the
    # first rejection. Negative values count as disabled (0). An ordering
    # misconfiguration (escalate >= block while both enabled) silently skips
    # escalation — a call would be refused without ever carrying the escalated
    # warning — so it is flagged once per process.

    _warned_threshold_order = False

    @staticmethod
    def _threshold(name: str, default: int) -> int:
        from cuga.config import settings

        try:
            value = int(getattr(settings.advanced_features, name, default))
        except (TypeError, ValueError):
            logger.warning(f"advanced_features.{name} is not an integer; using default {default}")
            return default
        return max(0, value)

    @classmethod
    def _escalate_after(cls) -> int:
        return cls._threshold("rejected_call_escalate_after", 1)

    @classmethod
    def _block_after(cls) -> int:
        block_after = cls._threshold("rejected_call_block_after", 2)
        escalate_after = cls._threshold("rejected_call_escalate_after", 1)
        if block_after and escalate_after and escalate_after >= block_after:
            if not RejectedCallGuard._warned_threshold_order:
                RejectedCallGuard._warned_threshold_order = True
                logger.warning(
                    f"rejected_call_escalate_after ({escalate_after}) >= rejected_call_block_after "
                    f"({block_after}): identical calls will be refused without ever receiving the "
                    f"escalated warning. Set escalate_after < block_after."
                )
        return block_after

    # ── Signature ──────────────────────────────────────────────────────────

    @staticmethod
    def signature(
        app_name: str,
        function_name: str,
        args: Optional[Dict[str, Any]],
        agent_id: Optional[str] = None,
    ) -> str:
        """Canonical, order-insensitive key for one logical call."""
        normalized = {k: v for k, v in (args or {}).items() if k not in _SIGNATURE_IGNORED_KEYS}
        return json.dumps(
            {"agent": agent_id, "app": app_name, "fn": function_name, "args": normalized},
            sort_keys=True,
            default=str,
        )

    # ── Guard operations ───────────────────────────────────────────────────

    def check(
        self,
        app_name: str,
        function_name: str,
        args: Optional[Dict[str, Any]],
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return a short-circuit error response if this exact call must not run.

        ``None`` means the call may proceed. The returned dict mirrors the
        registry's standard exception shape (same ``status_code`` as the stored
        rejection), so callers can serve it exactly like a real API rejection.
        """
        block_after = self._block_after()
        if not block_after:
            return None
        key = self.signature(app_name, function_name, args, agent_id)
        with self._lock:
            entry = self._rejections.get(key)
            if entry is None or entry.count < block_after:
                return None
        logger.warning(
            f"Short-circuiting '{function_name}' ({app_name}): identical call already "
            f"rejected {entry.count} times with HTTP {entry.status_code}"
        )
        return {
            "status": "exception",
            "served_as_http_error": entry.served_as_http_error,
            "status_code": entry.status_code,
            "message": (
                f"Not executed: this exact call (same endpoint, same arguments) was already "
                f"rejected {entry.count} times with: {entry.message} — re-issuing it unchanged "
                f"will never succeed. Change the arguments, or take a different action: if the "
                f"error describes a fixable precondition (e.g. insufficient balance, a missing "
                f"resource), fix that first with a different call and then retry this one. If "
                f"nothing can make it succeed, state that this step cannot be completed."
            ),
            "error_type": "RepeatedRejectedCall",
            "function_name": function_name,
        }

    def record_rejection(
        self,
        app_name: str,
        function_name: str,
        args: Optional[Dict[str, Any]],
        status_code: Optional[int],
        message: str,
        agent_id: Optional[str] = None,
        served_as_http_error: bool = True,
    ) -> Optional[str]:
        """Record a rejected call; return an escalated message when due.

        Only guarded 4xx statuses count. Returns ``None`` when the original
        message should be served unchanged (first rejection, non-guarded status,
        or escalation disabled). ``served_as_http_error=False`` marks rejections
        that reach the client as HTTP 200 with an exception-shaped body (the
        AppWorld adapter path); a later short-circuit mirrors that flavor.
        """
        if status_code not in GUARDED_STATUS_CODES:
            return None
        key = self.signature(app_name, function_name, args, agent_id)
        with self._lock:
            entry = self._rejections.get(key)
            if entry is None:
                entry = self._rejections[key] = _Rejection(0, status_code, message)
            entry.count += 1
            entry.status_code = status_code
            entry.message = message
            entry.served_as_http_error = served_as_http_error
            count = entry.count
        escalate_after = self._escalate_after()
        if not escalate_after or count <= escalate_after:
            return None
        return (
            f"[Repeated failure] This exact call (same endpoint, same arguments) has now been "
            f"rejected {count} times with the same class of error. Do not re-issue it unchanged — "
            f"change the arguments or the approach. Error: {message}"
        )

    def record_success(self, app_name: str, method: Optional[str]) -> None:
        """Clear all rejection counters after a successful mutating call.

        A missing/unknown method is treated as mutating: wrongly clearing only
        weakens the guard for a while, whereas wrongly keeping a block could
        forbid a call that has become valid.
        """
        if (method or "").upper() in ("GET", "HEAD"):
            return
        with self._lock:
            if self._rejections:
                logger.debug(
                    f"Clearing {len(self._rejections)} rejected-call signatures after "
                    f"successful mutating call to '{app_name}'"
                )
                self._rejections.clear()

    def reset(self) -> None:
        """Forget everything (task boundary — wired into ``/api/reset``)."""
        with self._lock:
            self._rejections.clear()


# Module-level singleton used by the registry server.
rejected_call_guard = RejectedCallGuard()
