"""The shared reflection prompt must keep its category-membership rule.

Deciding "all X" by a keyword in an item's name both under- and over-selects:
the keyword misses members whose name words differ and catches non-members that
happen to contain it. The rule that forbids it is one bullet in a prompt file,
so dropping it fails nothing — see the regression in #1110, where the rule went
with the prompt simplification in #1102.
"""

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.reflection import reflection_prompt

pytestmark = pytest.mark.unit


def _system_text() -> str:
    template = reflection_prompt()
    return "\n".join(str(getattr(m, "prompt", m).template) for m in template.messages if hasattr(m, "prompt"))


def test_category_membership_rule_is_present():
    text = _system_text()
    assert "**Type or category**" in text
    assert "never decide membership by substring-matching" in text


def test_category_rule_names_the_structured_field_and_the_all_x_wording():
    text = _system_text()
    assert "authoritative structured field" in text
    assert "the set is defined by the field, not by the wording" in text


def test_the_structured_field_rule_is_scoped_to_types_and_categories():
    """The "all X" override only applies when X is a type or a category.

    Unscoped, the bullet reads as covering a task whose set really is defined
    by name text, which is the over-reach #1102's flat tips list introduced.
    """
    text = _system_text()
    assert 'and X names a type or category' in text


def test_an_explicit_name_or_description_criterion_is_still_honoured():
    """A task saying "all files whose name contains draft" means that text."""
    text = _system_text()
    assert "spells out a name or description criterion" in text
    assert "you filter on it as written" in text


def test_broad_keyword_search_tip_is_retained():
    """The selection rule narrows; the discovery tip must still broaden.

    An over-specific query returns nothing, so the keyword tip has to survive
    alongside the rule that keeps its results from deciding membership.
    """
    text = _system_text()
    assert "**Keyword Search Strategy**" in text
    assert "use broader search terms to catch more values" in text
