"""Offline unit tests for the ACTION half (design: events_docs/plans/TRIGGERS_ACTIONS_DESIGN.md).

Pure/dependency-free like the trigger tests — no live AP, no LLM. Covers the action registry,
param rendering, resolve_action (tool-first/AP-fallback), the action_step renderer, the Option-B
branch predicates, and the flow assembler (sequential actions + branches + approval gate).
"""

import os
import sys

_EVENTS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "src", "cuga", "backend", "events"))
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import actions            # noqa: E402
import flows              # noqa: E402


# ── registry lookup / aliases / default ─────────────────────────────────────────────────────────
def test_get_by_name_and_alias_and_default():
    assert actions.get("gmail", "send_email").ap_action == "send_email"
    assert actions.get("gmail", "reply").name == "reply_to_email"        # alias
    assert actions.get("gmail", "draft").name == "create_draft_reply"    # alias
    assert actions.get("gmail", "").name == "send_email"                 # app default
    assert actions.get("gmail", "delete_email") is None                  # not in native pilot


def test_gmail_pilot_is_native_only():
    names = {a.name for a in actions.actions_for("gmail")}
    assert names == {"send_email", "reply_to_email", "create_draft_reply"}
    # D1: no destructive gmail actions ship in the pilot
    assert all(not a.destructive for a in actions.actions_for("gmail"))


# ── validate: unknown / missing user slot / armable ─────────────────────────────────────────────
def test_validate_unknown_action_is_hard_error():
    a, problem = actions.validate("gmail", "delete_everything")
    assert a is None and "unknown action" in problem


def test_validate_asks_for_missing_user_slot():
    a, problem = actions.validate("gmail", "send_email", {})       # receiver is a user slot
    assert a is not None and problem                               # asks, doesn't reject
    assert "email" in problem.lower()


def test_validate_ok_when_user_slot_supplied():
    a, problem = actions.validate("gmail", "send_email", {"receiver": ["me@x.com"]})
    assert a is not None and problem == ""


def test_validate_reply_needs_no_user_slot():
    # reply/draft key off the trigger message id (auto-filled) — armable with no user input
    assert actions.validate("gmail", "reply_to_email", {})[1] == ""
    assert actions.validate("gmail", "create_draft_reply", {})[1] == ""


# ── render_params: template / static / answer / array / override ─────────────────────────────────
def test_render_params_sources():
    p = actions.render_params(actions.get("gmail", "reply_to_email"))
    assert p["message_id"] == "{{trigger.message.id}}"        # trigger template
    assert p["body"] == "{{step_1.body.answer}}"              # answer
    assert p["reply_type"] == "reply"                         # static default
    assert p["body_type"] == "plain_text"


def test_render_params_array_and_override():
    p = actions.render_params(actions.get("gmail", "send_email"),
                              {"receiver": "boss@x.com", "subject": "Hi"})
    assert p["receiver"] == ["boss@x.com"]                    # ARRAY wrap
    assert p["subject"] == "Hi"                               # override beats static default
    assert p["body"] == "{{step_1.body.answer}}"
    assert p["draft"] is False


# ── resolve_action: tool-first, AP-fallback, ask ────────────────────────────────────────────────
def test_resolve_action_ap_fallback():
    how, obj = actions.resolve_action("gmail", "send_email", agent_tool_names=[])
    assert how == "ap" and obj.name == "send_email"


def test_resolve_action_prefers_agent_tool():
    how, tool = actions.resolve_action("gmail", "send_email",
                                       agent_tool_names=["gmail_send_email", "current_time"])
    assert how == "tool" and tool in ("gmail_send_email", "send_email")


def test_resolve_action_unknown_asks():
    how, problem = actions.resolve_action("gmail", "nope")
    assert how == "ask" and "unknown" in problem


# ── extract_action: the deterministic NL on-ramp ────────────────────────────────────────────────
def test_extract_action_reply_draft_send():
    assert actions.extract_action("when I get an email, reply to the sender")[0] == "gmail/reply_to_email"
    assert actions.extract_action("draft a reply summarizing it")[0] == "gmail/create_draft_reply"
    a, to = actions.extract_action("email me a summary at me@x.com")
    assert a == "gmail/send_email" and to == "me@x.com"
    a, to = actions.extract_action("reply to the sender")
    assert a == "gmail/reply_to_email"
    a, to = actions.extract_action("email me about it")
    assert a == "gmail/send_email" and to == "me"


def test_extract_action_no_false_positives():
    # plain delivery is NOT an action
    assert actions.extract_action("summarize it and message me") == (None, None)
    assert actions.extract_action("ping me on any move") == (None, None)
    assert actions.extract_action("tell me only if it rains") == (None, None)
    # "draft PR" must NOT match create_draft_reply (the bare \bdraft\b false-match, now fixed)
    assert actions.extract_action("when a draft PR opens on o/r, notify me") == (None, None)


def test_extract_action_send_to_sender():
    a, to = actions.extract_action("when an email arrives, email the sender back")
    assert a == "gmail/send_email" and to == "sender"


# ── action_step renderer ────────────────────────────────────────────────────────────────────────
def test_action_step_renders_piece_and_input():
    step = flows.action_step("gmail", "reply_to_email",
                             actions.render_params(actions.get("gmail", "reply_to_email")))
    assert step["type"] == "PIECE"
    assert step["settings"]["pieceName"] == "@activepieces/piece-gmail"
    assert step["settings"]["actionName"] == "reply_to_email"
    assert step["settings"]["input"]["message_id"] == "{{trigger.message.id}}"


def test_action_step_unknown_raises():
    try:
        flows.action_step("gmail", "delete_all", {})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unknown action" in str(e)


def test_send_step_gmail_folds_into_action_step():
    step = flows.send_step("gmail", "me@x.com", "hello")
    assert step["settings"]["actionName"] == "send_email"
    assert step["settings"]["input"]["receiver"] == ["me@x.com"]
    assert step["settings"]["input"]["body"] == "hello"


# ── router_step: legacy + Option-B predicates ───────────────────────────────────────────────────
def test_router_legacy_match_shape_unchanged():
    r = flows.router_step([{"name": "MATCH", "match": "MATCH", "action": None},
                           {"name": "else", "match": None, "action": None}])
    b0 = r["settings"]["branches"][0]
    assert b0["branchType"] == "CONDITION"
    assert b0["conditions"][0][0]["operator"] == "TEXT_STARTS_WITH"
    assert b0["conditions"][0][0]["firstValue"] == "{{step_1.body.answer}}"
    assert r["settings"]["branches"][1]["branchType"] == "FALLBACK"


def test_router_predicate_answer_contains():
    r = flows.router_step([{"name": "urgent",
                            "when": {"field": "answer", "op": "CONTAINS", "value": "urgent"},
                            "action": None},
                           {"name": "else", "action": None}])
    c = r["settings"]["branches"][0]["conditions"][0][0]
    assert c["operator"] == "TEXT_CONTAINS" and c["secondValue"] == "urgent"


def test_router_predicate_trigger_field_number():
    r = flows.router_step([{"name": "big",
                            "when": {"field": "trigger.pull_request.changed_files",
                                     "op": "GT", "value": 300}, "action": None},
                           {"name": "else", "action": None}])
    c = r["settings"]["branches"][0]["conditions"][0][0]
    assert c["operator"] == "NUMBER_IS_GREATER_THAN"
    assert c["firstValue"] == "{{trigger.pull_request.changed_files}}"
    assert "caseSensitive" not in c                       # number op, not text


# ── flow assembler: sequential actions + branches + approval ─────────────────────────────────────
def test_build_action_tail_sequential_chains_and_renumbers():
    a1 = flows.action_step("gmail", "reply_to_email",
                           actions.render_params(actions.get("gmail", "reply_to_email")))
    a2 = flows.send_step("slack", "#ops", "{{step_1.body.answer}}")
    head = flows.build_action_tail(actions=[a1, a2])
    assert head["name"] == "step_2"
    assert head["nextAction"]["name"] == "step_3"
    assert head["nextAction"]["nextAction"] is None


def test_build_action_tail_inserts_approval_gate():
    a1 = flows.action_step("gmail", "send_email",
                           actions.render_params(actions.get("gmail", "send_email"),
                                                 {"receiver": ["me@x.com"]}))
    a1["_approve"] = True
    head = flows.build_action_tail(actions=[a1])
    assert head["settings"]["pieceName"] == "@activepieces/piece-approval"
    assert head["nextAction"]["settings"]["actionName"] == "send_email"


def test_build_push_flow_with_action():
    a1 = flows.action_step("gmail", "reply_to_email",
                           actions.render_params(actions.get("gmail", "reply_to_email")))
    flow = flows.build_push_flow(agent="mailbot", thread_id="t", prompt="reply to it",
                                 source="gmail", event_kind="new_email", actions=[a1])
    assert flow["valid"]
    invoke = flow["trigger"]["nextAction"]
    assert invoke["name"] == "step_1"
    assert invoke["nextAction"]["settings"]["actionName"] == "reply_to_email"


def test_build_push_flow_with_branches_still_works():
    # the resume-watcher shape (legacy branches) must be unchanged
    flow = flows.build_push_flow(agent="resume_judge", thread_id="t", prompt="judge",
                                 source="box", event_kind="new_file",
                                 branches=[{"name": "MATCH", "match": "MATCH",
                                            "action": flows.send_step("gmail", "x@y.com", "hi")},
                                           {"name": "else", "match": None, "action": None}])
    router = flow["trigger"]["nextAction"]["nextAction"]
    assert router["type"] == "ROUTER"
    assert router["settings"]["branches"][0]["branchType"] == "CONDITION"
