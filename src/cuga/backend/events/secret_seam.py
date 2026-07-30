"""Events-layer secret access — one seam, vault-capable, plaintext-compatible.

Every credential the events layer reads (AP_PASSWORD, bot tokens, BOX_DEV_TOKEN, OAuth client
secrets, GATEWAY_TOKEN, …) goes through :func:`secret` instead of a raw ``os.environ.get``. The
value in ``.env`` may then be either:

  * a plaintext secret (dev; unchanged behavior), or
  * a **reference** into CUGA's secret-resolver backends — ``vault://path``, ``aws://name``,
    ``db://key`` or ``env://OTHER_VAR`` (see ``cuga.backend.secrets``) — so production never keeps
    the actual credential in a dotfile. E.g. ``AP_PASSWORD=vault://events/ap_password``.

Only values that LOOK like a reference (contain ``://``) are routed through the resolver — a
plaintext password that happens to be all-caps must never be misread as an env-var name, so we do
NOT use the resolver's bare-name convention here. This keeps the change 100% backward-compatible.

The helper also strips the ``.env`` idioms the layer already tolerates (trailing ``# comments``,
surrounding quotes) in ONE place instead of five slightly-different copies.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("cuga.events.secrets")


def _clean(raw: str) -> str:
    return (raw or "").split(" #", 1)[0].strip().strip('"').strip("'")


def secret(name: str, default: str = "") -> str:
    """Read a secret env var; dereference it through CUGA's secret resolver when it is a
    ``scheme://`` reference. Returns "" (or ``default``) when unset/unresolvable — and logs the
    unresolvable case, because a silently-empty credential is the worst failure mode."""
    raw = _clean(os.environ.get(name, default))
    if "://" not in raw:
        return raw
    try:
        from cuga.backend.secrets import resolve_secret
        val = resolve_secret(raw)
        if val is None:
            log.warning("secret %s is a reference (%s…) but resolved to nothing — "
                        "check the secrets backend", name, raw.split("://", 1)[0])
            return ""
        return val
    except Exception as e:  # noqa: BLE001 — resolver missing/broken must not crash the caller
        log.warning("secret %s: resolver failed (%s) — treating as unset", name, e)
        return ""
