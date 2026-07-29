"""New trigger pieces — Google Calendar, Pinterest, YouTube (AP) + a Discord reaction (direct).

Proves the "adding a piece = adding data" claim end to end at the registry level: each new trigger
is enumerable, classifiable, has a valid payload map, resolves its slots, and (direct) maps its
transport event. Live arming additionally needs the piece's AP connection — out of scope here.
"""
import os
import sys
import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events"))

import classify  # noqa: E402
import direct_events  # noqa: E402
import discord_direct  # noqa: E402
import triggers as T  # noqa: E402
from envelope import EVENT_KINDS  # noqa: E402


# ── the rows exist with the right backend/piece/ap_trigger ───────────────────────────────────────
# {(app, event): (ap_trigger, ap_piece_key)} — app is our canonical name, piece is the AP piece key
# (they differ for google_calendar → google-calendar so the app stays [a-z_] for the roster gate).
NEW_AP = {
    ("google_calendar", "new_event"): ("new_event", "google-calendar"),
    ("google_calendar", "new_or_updated_event"): ("new_or_updated_event", "google-calendar"),
    ("google_calendar", "event_ends"): ("event_ends", "google-calendar"),
    ("pinterest", "new_pin"): ("newPinOnBoard", "pinterest"),
    ("pinterest", "new_board"): ("newBoard", "pinterest"),
    ("pinterest", "new_follower"): ("newFollower", "pinterest"),
    ("youtube", "new_video"): ("new-video", "youtube"),
    ("rss", "new_item"): ("new-item", "rss"),
}


def test_ap_rows_present_with_correct_ap_trigger_name():
    for (app, event), (ap_trigger, piece) in NEW_AP.items():
        row = T.TRIGGERS.get((app, event))
        assert row is not None, f"missing {(app, event)}"
        assert row.backend == "ap"
        assert row.piece == piece, f"{app}/{event}: piece {row.piece} != {piece}"
        assert row.ap_trigger == ap_trigger, f"{app}/{event}: {row.ap_trigger} != {ap_trigger}"


def test_ap_payload_maps_use_trigger_templates():
    """Curated paths must be {{trigger.*}} templates so the /invoke body carries real content
    (with _raw always added by ap_engine as the net)."""
    for key in NEW_AP:
        row = T.TRIGGERS[key]
        assert row.payload, f"{key} has no payload map"
        for v in row.payload.values():
            assert v.startswith("{{trigger."), f"{key}: {v!r} is not a trigger template"


def test_each_new_app_has_exactly_one_default():
    for app in ("google_calendar", "pinterest", "youtube"):
        defaults = [t for t in T.rows() if t.app == app and t.default]
        assert len(defaults) == 1, f"{app} defaults: {[d.event for d in defaults]}"


# ── the Discord reaction is a DIRECT trigger, wired into the gateway ─────────────────────────────
def test_discord_reaction_is_direct_and_maps_its_transport_kind():
    row = T.TRIGGERS.get(("discord", "new_reaction"))
    assert row is not None and row.backend == "direct"
    assert row.direct_kind == "MESSAGE_REACTION_ADD"
    assert direct_events.kind_for("discord", "MESSAGE_REACTION_ADD") == "new_reaction"


def test_discord_reactions_intent_is_enabled_and_non_privileged():
    # bit 10 = GUILD_MESSAGE_REACTIONS. Must be on by default (unlike GUILD_MEMBERS, bit 1).
    assert discord_direct.INTENTS & (1 << 10), "reactions intent not set"
    assert not (discord_direct.intents() & (1 << 1)), "members intent must stay opt-in"


# ── the classifier routes natural language to the new sources ────────────────────────────────────
def test_classifier_routes_to_new_sources():
    cases = {
        "notify me when a new event is added to my calendar": ("google_calendar", "new_event"),
        "when a meeting ends, email me the notes": ("google_calendar", "event_ends"),
        "when there's a new pin on my pinterest board, ping me": ("pinterest", "new_pin"),
        "tell me about a new pinterest follower": ("pinterest", "new_follower"),
        "when my youtube channel posts a new video, share it": ("youtube", "new_video"),
        "when someone reacts in discord with :tada:, thank them": ("discord", "new_reaction"),
    }
    for text, expected in cases.items():
        assert classify.source_of(text) == expected, f"{text!r} -> {classify.source_of(text)}"


def test_discord_reaction_beats_slack_only_when_discord_named():
    assert classify.source_of("react with :bug: in discord") == ("discord", "new_reaction")
    assert classify.source_of("react with :bug: in slack") == ("slack", "new_reaction")


# ── slot extraction pulls the required config out of the utterance ───────────────────────────────
def test_slot_extraction_for_new_pieces():
    import flowspec
    assert flowspec.extract_slots("youtube", "new_video",
                                  "watch @Fireship for new videos")["yt_channel"] == "@Fireship"
    assert flowspec.extract_slots("youtube", "new_video",
                                  "watch https://youtube.com/@mkbhd for uploads")["yt_channel"].startswith("https://")
    assert flowspec.extract_slots("rss", "new_item",
                                  "watch https://blog.example.com/rss for new items")["rss_feed_url"] \
        == "https://blog.example.com/rss"
    assert flowspec.extract_slots("pinterest", "new_pin",
                                  "new pin on board 549755885175")["board"] == "549755885175"
    assert flowspec.extract_slots("google_calendar", "new_event",
                                  "watch my primary calendar")["calendar"] == "primary"


# ── slots + envelope kinds derive automatically ──────────────────────────────────────────────────
def test_new_slots_are_defined():
    for name in ("calendar", "board", "yt_channel"):
        assert name in T.SLOTS, f"slot {name} not defined"
    # every slot a new row references must resolve
    for key in list(NEW_AP) + [("discord", "new_reaction")]:
        for s in T.TRIGGERS[key].slots:
            assert s in T.SLOTS, f"{key} references undefined slot {s}"


def test_event_kinds_include_the_new_events():
    for _, event in list(NEW_AP) + [("discord", "new_reaction")]:
        assert event in EVENT_KINDS, f"{event} missing from EVENT_KINDS"


# ── the default AP triggers carry a synth sample consistent with their payload map ───────────────
def _dig(obj, path):
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def test_default_ap_triggers_have_a_consistent_synth_sample():
    """A synth payload lets these be fired at the /invoke seam WITHOUT a live connection. Every
    curated {{trigger.<path>}} in the payload map must resolve in the synth sample — a path typo
    (the classic 'flow ran green but the agent got nothing') fails here, offline."""
    import re
    for app in ("google_calendar", "pinterest", "youtube", "rss"):
        row = next(t for t in T.rows() if t.app == app and t.default)
        assert row.synth, f"{app} default trigger has no synth sample"
        for tmpl in row.payload.values():
            m = re.fullmatch(r"\{\{trigger\.([\w.]+)\}\}", tmpl)
            if not m:
                continue
            assert _dig(row.synth, m.group(1)) is not None, \
                f"{app}: synth is missing path {m.group(1)!r} used by the payload map"
