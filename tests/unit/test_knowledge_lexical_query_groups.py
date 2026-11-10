"""Keyword query groups: repeated groups are dropped and long queries are capped."""

import pytest

from cuga.backend.knowledge import lexical

pytestmark = pytest.mark.unit


def test_repeated_words_make_one_group():
    assert lexical.query_groups("backup the backup ABC-123 abc-123") == [["backup"], ["the"], ["abc", "123"]]


def test_a_pasted_chinese_paragraph_is_capped():
    paragraph = "".join(chr(0x4E00 + i) for i in range(200))  # 200 distinct characters, 199 pairs
    groups = lexical.query_groups(paragraph)
    assert len(groups) == lexical._MAX_QUERY_GROUPS
    assert len({tuple(g) for g in groups}) == len(groups)


def test_repeated_chinese_pairs_are_counted_once():
    sentence = "我们的数据备份策略每天执行一次"
    assert lexical.query_groups(" ".join([sentence] * 5)) == lexical.query_groups(sentence)


@pytest.mark.parametrize("query", ["Tyler MacDuff's child", "Tyler MacDuff\u2019s child"])
def test_a_possessive_still_matches_the_bare_name(query):
    """A possessive must not join the name into a phrase ("macduff s" missed "MacDuff"), and its
    one-letter tail must not become a group of its own: "s" matches nearly every chunk."""
    assert lexical.query_groups(query) == [["tyler"], ["macduff"], ["child"]]


@pytest.mark.parametrize(
    ("query", "groups"),
    [
        ("office(s)", [["office"]]),
        ("l'homme", [["homme"]]),
        ("rock'n'roll", [["rock"], ["roll"]]),
        ("Plan B", [["plan"], ["b"]]),
        ("C", [["c"]]),
    ],
)
def test_one_letter_tails_of_a_longer_word_are_dropped(query, groups):
    """A one-letter part of a longer word (a possessive, a plural in parentheses, an elided
    article) is dropped; a word that is one letter on its own is kept."""
    assert lexical.query_groups(query) == groups


@pytest.mark.parametrize(
    ("query", "groups"),
    [
        ("Paris/London", [["paris"], ["london"]]),
        ("Paris,London", [["paris"], ["london"]]),
        ("Paris+London", [["paris"], ["london"]]),
        ("Paris|London", [["paris"], ["london"]]),
        ("IPv4/IPv6", [["ipv4"], ["ipv6"]]),
        ("Node.js/Python", [["node", "js"], ["python"]]),
        ("GPT-4/Claude", [["gpt", "4"], ["claude"]]),
        ("2023/2024", [["2023"], ["2024"]]),
    ],
)
def test_words_joined_by_ordinary_punctuation_match_separately(query, groups):
    """Matched as one phrase ("paris london"), these found neither a Paris nor a London document.
    Each part keeps its own identifier phrase ("node.js")."""
    assert lexical.query_groups(query) == groups


@pytest.mark.parametrize(
    ("query", "phrase"),
    [
        ("ABC-123", ["abc", "123"]),
        ("ＡＢＣ－１２３", ["abc", "123"]),
        ("abc_def", ["abc", "def"]),
        ("user@example.com", ["user", "example", "com"]),
        ("engine.py", ["engine", "py"]),
        ("10:30", ["10", "30"]),
        ("(ABC-123)", ["abc", "123"]),
        ("AT&T", ["at", "t"]),
        ("R&D", ["r", "d"]),
        ("I/O", ["i", "o"]),
        ("A/B", ["a", "b"]),
        ('דו"ח', ["דו", "ח"]),
        ("צה״ל", ["צה", "ל"]),
        ("מנכ׳ל", ["מנכ", "ל"]),
    ],
)
def test_identifiers_stay_phrases(query, phrase):
    """Names and acronyms written with punctuation, and words made only of single letters joined by
    any punctuation, stay one phrase: split, a one-letter term matches almost every document."""
    assert lexical.query_groups(query) == [phrase]
