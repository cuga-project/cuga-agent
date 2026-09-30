"""Provider-safe ("wire") tool names for native function calling.

Bedrock's Converse API rejects any ``toolSpec.name`` longer than 64 characters with
a 400 that fails the whole model call — not a per-tool skip. AppWorld's registered
names routinely exceed it once prefixed with the app name, e.g.
``amazon_move_product_from_cart_to_wish_list_cart_to_wish_list_product_id_post`` (77).
CodeAct never binds tools natively, so it never hit this; function calling does.

Names are mapped only at the native boundary: the wire name is what the provider
and the model see (bound specs, tool_search results, hints), and ``tool_exec`` maps
it back to the registered tool. Registry names — and everything CodeAct sees — are
unchanged. The scheme (truncate + 8-hex sha1 of the full name) matches the registry
cap on ``feature/hybrid-cuga-agent`` so both produce the same names.
"""

from __future__ import annotations

import hashlib

MAX_WIRE_NAME_LENGTH = 64


def wire_name(name: str) -> str:
    """``name`` if it fits the provider limit, else a deterministic unique short form."""
    if len(name) <= MAX_WIRE_NAME_LENGTH:
        return name
    suffix = hashlib.sha1(name.encode()).hexdigest()[:8]
    keep = MAX_WIRE_NAME_LENGTH - len(suffix) - 1
    return f"{name[:keep].rstrip('_')}_{suffix}"
