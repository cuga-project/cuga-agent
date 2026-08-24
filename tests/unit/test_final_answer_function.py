"""Unit tests for the configurable final answer (#621): the deterministic
answer function, its ordering inside FinalAnswerNode.finalize_answer, and
the instructions override."""

from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.answer.answer_function import (
    FinalAnswerConfig,
    apply_answer_function,
    resolve_answer_function,
    resolve_final_answer_instructions,
)
from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.knowledge.sources import _reset_all_ledgers_for_tests, get_ledger
from cuga.config import settings

pytestmark = pytest.mark.unit


class _State(SimpleNamespace):
    pass


def _state(answer, thread_id="t-fn"):
    return _State(final_answer=answer, thread_id=thread_id, sources=[])


def setup_function():
    _reset_all_ledgers_for_tests()


@pytest.fixture(autouse=True)
def _clean_final_answer_settings():
    """Force-empty the [final_answer] settings for every test, restore after.

    Force-set (not just restore): a developer's DYNACONF_FINAL_ANSWER__*
    env vars must not leak into tests that assert empty-settings behavior.
    """
    before_fn = settings.final_answer.function
    before_instr = settings.final_answer.instructions
    settings.set("final_answer.function", "")
    settings.set("final_answer.instructions", "")
    yield
    settings.set("final_answer.function", before_fn)
    settings.set("final_answer.instructions", before_instr)


def _strip_brackets(text: str) -> str:
    """Reference contract function: idempotent bare-value normalizer."""
    out = text.strip()
    while out.startswith("[[") and out.endswith("]]"):
        out = out[2:-2].strip()
    return out


# --- defaults: byte-identical behavior -------------------------------------


def test_defaults_leave_answer_untouched():
    assert settings.final_answer.function == ""
    assert settings.final_answer.instructions == ""
    state = _state("[[New Hampshire]] *raw* text")
    FinalAnswerNode.finalize_answer(state)
    assert state.final_answer == "[[New Hampshire]] *raw* text"
    assert state.sources == []


# --- the function: settings path and injected callable ----------------------


def test_settings_dotted_path_applies():
    settings.set("final_answer.function", "json.dumps")
    state = _state("hello")
    FinalAnswerNode.finalize_answer(state)
    assert state.final_answer == '"hello"'


def test_injected_callable_applies_and_wins_over_settings():
    settings.set("final_answer.function", "json.dumps")
    state = _state("[[42]]")
    FinalAnswerNode.finalize_answer(state, _strip_brackets)
    assert state.final_answer == "42"


def test_idempotent_function_double_application_is_stable():
    # The seam applies the function once per delivered answer; idempotency is
    # a recommended safety margin, and this locks that an idempotent function
    # survives an accidental second pass unchanged.
    state = _state("[[42]]")
    FinalAnswerNode.finalize_answer(state, _strip_brackets)
    FinalAnswerNode.finalize_answer(state, _strip_brackets)
    assert state.final_answer == "42"


# --- ordering ----------------------------------------------------------------


def test_function_receives_harmony_stripped_text():
    seen = {}

    def probe(text):
        seen["input"] = text
        return text

    state = _state("The total is 42<|return|>")
    FinalAnswerNode.finalize_answer(state, probe)
    assert seen["input"] == "The total is 42"
    assert state.final_answer == "The total is 42"


def test_function_sees_raw_citation_markers_not_resolved_chips():
    ledger = get_ledger("t-fn")
    ledger.register(
        SimpleNamespace(text="chunk", filename="f.pdf", page=1, scope="agent", score=0.9, section_path=""),
        query="q",
    )
    seen = {}

    def probe(text):
        seen["input"] = text
        return text

    state = _state("answer [s1] done")
    FinalAnswerNode.finalize_answer(state, probe)
    assert seen["input"] == "answer [s1] done"  # pre-citation
    assert state.final_answer == "answer [1] done"  # citations still resolve after


# --- failure paths: answer delivery must survive ----------------------------


def test_function_raising_delivers_original():
    def boom(_):
        raise RuntimeError("nope")

    state = _state("safe answer")
    FinalAnswerNode.finalize_answer(state, boom)
    assert state.final_answer == "safe answer"


def test_function_returning_non_str_is_ignored():
    state = _state("safe answer")
    FinalAnswerNode.finalize_answer(state, lambda _t: 42)
    assert state.final_answer == "safe answer"


def test_unresolvable_settings_path_delivers_original():
    settings.set("final_answer.function", "no.such.module.fn")
    state = _state("safe answer")
    FinalAnswerNode.finalize_answer(state)
    assert state.final_answer == "safe answer"


def test_empty_answer_skips_function():
    calls = []
    state = _state("")
    apply_answer_function(state, lambda t: calls.append(t) or t)
    assert calls == []
    assert state.final_answer == ""


def test_empty_string_return_is_a_valid_result():
    state = _state("refused")
    FinalAnswerNode.finalize_answer(state, lambda _t: "")
    assert state.final_answer == ""


# --- resolver ----------------------------------------------------------------


def test_resolver_rejects_non_callable():
    with pytest.raises(TypeError):
        resolve_answer_function("json.__name__")


def test_resolver_rejects_class_paths():
    # A class is callable but constructs an instance — silent never-formats.
    with pytest.raises(TypeError, match="is a class"):
        resolve_answer_function("collections.OrderedDict")


def test_settings_class_path_delivers_original():
    settings.set("final_answer.function", "collections.OrderedDict")
    state = _state("safe answer")
    FinalAnswerNode.finalize_answer(state)
    assert state.final_answer == "safe answer"


def test_non_str_settings_values_never_break_delivery():
    settings.set("final_answer.function", 1)
    settings.set("final_answer.instructions", 1)
    state = _state("safe answer")
    FinalAnswerNode.finalize_answer(state)  # must not raise
    assert state.final_answer == "safe answer"
    assert resolve_final_answer_instructions() == ""


# --- instructions override ----------------------------------------------------


def test_instructions_resolve_from_settings():
    settings.set("final_answer.instructions", "Answer with the bare value only.")
    assert resolve_final_answer_instructions() == "Answer with the bare value only."


def test_instructions_empty_when_unset():
    # Callers fall back lazily: resolve_final_answer_instructions() or get_instructions(...)
    assert resolve_final_answer_instructions() == ""


# --- SDK surface --------------------------------------------------------------


def test_sdk_rejects_class_passed_as_final_answer():
    # Classes are callable — a forgotten `()` must raise, not silently
    # install the class as the answer function.
    import cuga

    with pytest.raises(TypeError, match="pass an instance"):
        cuga.CugaAgent(final_answer=FinalAnswerConfig)
    with pytest.raises(TypeError, match="must be a str"):
        cuga.CugaAgent(final_answer=123)
    with pytest.raises(TypeError, match="not a class"):
        cuga.CugaAgent(final_answer=FinalAnswerConfig(function=dict))


def test_final_answer_config_dataclass_and_lazy_export():
    import cuga

    cfg = cuga.FinalAnswerConfig(instructions="be terse", function=_strip_brackets)
    assert cfg is not None
    assert isinstance(cfg, FinalAnswerConfig)
    assert cfg.function("[[x]]") == "x"
