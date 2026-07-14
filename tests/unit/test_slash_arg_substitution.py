"""Unit tests for the slash-command ``$ARGUMENTS`` substitution engine."""

import pytest

from cuga.backend.slash_commands.arg_substitution import (
    InvalidArgumentName,
    split_args,
    substitute,
    validate_arg_names,
)

pytestmark = pytest.mark.unit


# --- split_args ---------------------------------------------------------


def test_split_args_whitespace():
    assert split_args("a b c") == ["a", "b", "c"]


def test_split_args_empty():
    assert split_args("") == []
    assert split_args("   ") == []


def test_split_args_honors_quotes():
    assert split_args('a "b c" d') == ["a", "b c", "d"]


def test_split_args_unbalanced_quotes_falls_back():
    assert split_args('a "b c') == ["a", '"b', "c"]


# --- validate_arg_names -------------------------------------------------


def test_validate_arg_names_accepts_valid():
    validate_arg_names(["pr_number", "title", "_x", "a1"])


def test_validate_arg_names_rejects_numeric_only():
    with pytest.raises(InvalidArgumentName):
        validate_arg_names(["1"])
    with pytest.raises(InvalidArgumentName):
        validate_arg_names(["title", "42"])


def test_validate_arg_names_rejects_malformed():
    with pytest.raises(InvalidArgumentName):
        validate_arg_names(["has-dash"])
    with pytest.raises(InvalidArgumentName):
        validate_arg_names(["1abc"])


# --- positional $N (1-indexed) ------------------------------------------


def test_positional_substitution_is_one_indexed():
    assert substitute("first=$1 second=$2", "alpha beta") == "first=alpha second=beta"


def test_positional_out_of_range_is_empty():
    assert substitute("x=$3", "alpha beta") == "x="


def test_positional_zero_is_empty():
    assert substitute("x=$0", "alpha") == "x="


# --- indexed $ARGUMENTS[N] (0-indexed) ----------------------------------


def test_indexed_substitution_is_zero_indexed():
    assert substitute("a=$ARGUMENTS[0] b=$ARGUMENTS[1]", "alpha beta") == "a=alpha b=beta"


def test_indexed_out_of_range_is_empty():
    assert substitute("a=$ARGUMENTS[5]", "alpha") == "a="


# --- named $name --------------------------------------------------------


def test_named_substitution_binds_positionally():
    out = substitute("PR #$pr title $title", "123 hello", ["pr", "title"])
    assert out == "PR #123 title hello"


def test_named_missing_arg_is_empty():
    assert substitute("title=$title", "", ["title"]) == "title="


def test_undeclared_dollar_word_left_verbatim():
    # $HOME is not a declared arg name — it must survive untouched, while a
    # real placeholder in the same body still substitutes.
    assert substitute("$HOME/bin uses $1", "alpha", ["title"]) == "$HOME/bin uses alpha"


def test_undeclared_dollar_word_is_not_a_placeholder():
    # An undeclared $word does not count as "consuming" the args, so the
    # append-if-no-placeholder fallback still fires.
    assert substitute("cd $HOME", "alpha", ["title"]) == "cd $HOME\n\nARGUMENTS: alpha"


# --- bare $ARGUMENTS ----------------------------------------------------


def test_bare_arguments_is_raw_string_verbatim():
    assert substitute("run with: $ARGUMENTS", "a b c") == "run with: a b c"


def test_bare_arguments_preserves_quotes_in_raw():
    assert substitute("$ARGUMENTS", 'a "b c"') == 'a "b c"'


# --- four-pass precedence / combination ---------------------------------


def test_indexed_not_eaten_by_bare():
    # $ARGUMENTS[0] must resolve as indexed, not as bare followed by "[0]".
    assert substitute("$ARGUMENTS[0]", "alpha beta") == "alpha"


def test_combination_of_all_passes():
    body = "named=$who idx=$ARGUMENTS[1] pos=$1 all=$ARGUMENTS"
    out = substitute(body, "alpha beta", ["who"])
    assert out == "named=alpha idx=beta pos=alpha all=alpha beta"


# --- escaped \$ ---------------------------------------------------------


def test_escaped_dollar_yields_literal_and_is_not_substituted():
    assert substitute(r"cost is \$5 not $1", "alpha") == "cost is $5 not alpha"


def test_escaped_dollar_does_not_count_as_placeholder():
    # Body has only an escaped \$ — no real placeholder — so args get appended.
    out = substitute(r"price \$ARGUMENTS", "alpha beta")
    assert out == "price $ARGUMENTS\n\nARGUMENTS: alpha beta"


# --- append-if-no-placeholder fallback ----------------------------------


def test_append_fallback_when_no_placeholder():
    assert substitute("do the thing", "alpha beta") == "do the thing\n\nARGUMENTS: alpha beta"


def test_no_append_when_placeholder_present():
    assert substitute("do $1", "alpha") == "do alpha"


def test_no_append_when_args_empty():
    assert substitute("do the thing", "") == "do the thing"


def test_empty_args_substitutes_placeholders_to_empty():
    assert substitute("a=$1 b=$ARGUMENTS c=$x", "", ["x"]) == "a= b= c="
