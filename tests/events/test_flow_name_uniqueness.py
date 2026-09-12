"""AP push-flow names must stay unique when flow reuse is disabled (Sami, PR 603 #2, P1).

The reported bug: the flow-name discriminator was `sha1(dedup_key)[:8]`, but `dedup_key` is
deliberately cleared to "" whenever EVENTS_FLOW_REUSE is off — which is the DEFAULT. sha1("") is
`da39a3ee`, so every watch got the same suffix. Two GitHub repos under one scope both produced
`push-github-new-pr-cuga-da39a3ee`, and `APEngine._new_flow()` deletes an existing same-named flow
before creating its replacement: arming the second watcher destroyed the first watcher's AP flow
and left its subscription behind, pointing at nothing.

The fix separates the two jobs the value was doing — dedup ("armed this before?") and naming ("is
this flow distinct?") — so clearing the reuse key no longer takes naming with it.
"""

from __future__ import annotations

import hashlib

EMPTY_SHA1_8 = hashlib.sha1(b"").hexdigest()[:8]


def _identity(agent, source, cadence, cfg_tag, sink, task, owner):
    """The identity string concierge builds — mirrored so the test pins the SHAPE, not the code."""
    return f"{agent}|{source}|{cadence}|{cfg_tag}|{sink}|{task}|{owner}"


def _flow_name(source, event, agent, identity):
    disc = hashlib.sha1(identity.encode()).hexdigest()[:8]
    return f"push-{source}-{(event or 'default').replace('_', '-')}-{agent}-{disc}"


def test_the_empty_key_collision_is_what_it_looks_like():
    """Ground the premise: an empty discriminator really is the same for everything."""
    assert EMPTY_SHA1_8 == "da39a3ee"
    a = _flow_name("github", "new_pr", "cuga", "")
    b = _flow_name("github", "new_pr", "cuga", "")
    assert a == b == f"push-github-new-pr-cuga-{EMPTY_SHA1_8}"


def test_two_repos_get_different_flow_names():
    """Sami's exact reproduction: same agent, same event, DIFFERENT repo."""
    one = _identity("cuga", "github", "push", "repo=octo/one", "slack", "", "default/default/u1")
    two = _identity("cuga", "github", "push", "repo=octo/two", "slack", "", "default/default/u1")
    n1, n2 = _flow_name("github", "new_pr", "cuga", one), _flow_name("github", "new_pr", "cuga", two)
    assert n1 != n2, "two repos must not share an AP flow name — the second would delete the first"
    assert EMPTY_SHA1_8 not in n1 and EMPTY_SHA1_8 not in n2


def test_different_sinks_also_differ():
    """Two watchers with EMPTY config on the same source+event differed only by sink — the
    regression that destroyed a live gmail flow in 2026-07-23."""
    a = _identity("cuga", "gmail", "push", "", "slack", "", "default/default/u1")
    b = _identity("cuga", "gmail", "push", "", "telegram", "", "default/default/u1")
    assert _flow_name("gmail", "new_email", "cuga", a) != _flow_name("gmail", "new_email", "cuga", b)


def test_different_owners_differ():
    a = _identity("cuga", "github", "push", "repo=octo/one", "slack", "", "default/default/alice")
    b = _identity("cuga", "github", "push", "repo=octo/one", "slack", "", "default/default/bob")
    assert _flow_name("github", "new_pr", "cuga", a) != _flow_name("github", "new_pr", "cuga", b)


def test_the_same_intent_still_produces_the_same_name():
    """Naming must stay DETERMINISTIC — re-arming the identical intent should not spawn a twin."""
    i = _identity("cuga", "github", "push", "repo=octo/one", "slack", "", "default/default/u1")
    assert _flow_name("github", "new_pr", "cuga", i) == _flow_name("github", "new_pr", "cuga", i)


def test_concierge_hashes_the_identity_not_the_reuse_key():
    """Pin the actual source: the fix is only real if flow_identity — not dedup_key — is hashed."""
    import pathlib

    src = pathlib.Path("src/cuga/backend/events/concierge.py").read_text()
    assert "flow_identity = dedup_key" in src, "the naming identity must be captured before clearing"
    assert "sha1(flow_identity.encode())" in src, "the flow name must hash the identity"
    assert "sha1(dedup_key.encode())" not in src, "hashing the reuse key is the bug"


def test_reuse_disabled_still_clears_the_dedup_key():
    """The fix must NOT accidentally re-enable dedup — an empty key is what keeps the store's
    partial UNIQUE index (WHERE dedup_key != '') from ever colliding."""
    import pathlib

    src = pathlib.Path("src/cuga/backend/events/concierge.py").read_text()
    assert 'dedup_key = ""' in src
