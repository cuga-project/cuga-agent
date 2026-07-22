"""Pure argument-substitution engine for slash-invoked skills.

When a user types ``/<skill> a b c`` the skill author's SKILL.md body may
reference those args. This module implements Claude Code's four-pass
substitution, applied to the *raw* SKILL.md body before the ``load_skill``
wrapper layers on install/sandbox hints.

Four passes, in precedence order:

    1. named         ``$name``           — names declared in the skill's
                                           ``arguments`` frontmatter key,
                                           bound positionally to the args
    2. indexed       ``$ARGUMENTS[N]``    — 0-indexed positional
    3. positional    ``$N``               — 1-indexed positional ($1 = first)
    4. bare          ``$ARGUMENTS``        — the full raw arg string verbatim

``\\$`` is an escape: it yields a literal ``$`` and never triggers
substitution. If the body contains *no* placeholder of any kind and the user
supplied args, the args are appended as a trailing ``ARGUMENTS: ...`` line
(the "append if no placeholder" fallback).

Numeric-only argument names are rejected at registration time
(:func:`validate_arg_names`) because ``$1`` is already the positional syntax.

The substitution itself is a single tokenizing pass: each position in the
body matches exactly one token form, so a value substituted in one pass can
never be re-scanned by a later one. The alternation order in ``_TOKEN_RE``
encodes the four-pass precedence.
"""

from __future__ import annotations

import re
import shlex
from typing import List, Sequence

_NUMERIC_NAME_RE = re.compile(r"^\d+$")
_VALID_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Alternation order encodes four-pass precedence; see module docstring.
_TOKEN_RE = re.compile(
    r"""
      (?P<escaped>\\\$)
    | (?P<indexed>\$ARGUMENTS\[(?P<idx>\d+)\])
    | (?P<bare>\$ARGUMENTS\b)
    | \$(?P<named>[A-Za-z_][A-Za-z0-9_]*)
    | \$(?P<pos>\d+)
    """,
    re.VERBOSE,
)


class InvalidArgumentName(ValueError):
    """A skill declared an argument name that collides with positional syntax."""


def validate_arg_names(names: Sequence[str]) -> None:
    """Reject numeric-only or malformed argument names.

    Numeric-only names (``$1``) collide with the positional substitution
    syntax. Called at skill-registration time so a bad declaration fails the
    skill rather than producing surprising substitutions later.
    """
    for n in names:
        if _NUMERIC_NAME_RE.match(n):
            raise InvalidArgumentName(
                f"Argument name {n!r} is numeric-only; it collides with positional $N syntax"
            )
        if not _VALID_NAME_RE.match(n):
            raise InvalidArgumentName(
                f"Argument name {n!r} is invalid; use letters, digits and underscore, "
                "and do not start with a digit"
            )
        if n.upper() == "ARGUMENTS":
            raise InvalidArgumentName(
                f"Argument name {n!r} is reserved; $ARGUMENTS denotes the full raw-args substitution"
            )


def split_args(raw_args: str) -> List[str]:
    """Split a raw arg string into positional tokens, honoring shell-style quoting."""
    raw_args = raw_args.strip()
    if not raw_args:
        return []
    try:
        return shlex.split(raw_args)
    except ValueError:
        # Unbalanced quotes — fall back to naive whitespace split.
        return raw_args.split()


def substitute(body: str, raw_args: str, arg_names: Sequence[str] = ()) -> str:
    """Apply four-pass argument substitution to a raw skill body.

    ``arg_names`` are the named arguments declared in skill frontmatter; they
    bind positionally to ``raw_args`` (the i-th declared name takes the i-th
    positional arg). A declared name with no corresponding positional, or an
    out-of-range index/positional, substitutes to the empty string.

    If the body contains no recognized placeholder and ``raw_args`` is
    non-empty, the args are appended as a trailing ``ARGUMENTS:`` line.
    """
    positionals = split_args(raw_args)
    named = {name: (positionals[i] if i < len(positionals) else "") for i, name in enumerate(arg_names)}

    found_placeholder = False

    def repl(m: re.Match) -> str:
        nonlocal found_placeholder
        if m.group("escaped"):
            return "$"
        if m.group("indexed") is not None:
            found_placeholder = True
            idx = int(m.group("idx"))
            return positionals[idx] if idx < len(positionals) else ""
        if m.group("bare") is not None:
            found_placeholder = True
            return raw_args
        named_tok = m.group("named")
        if named_tok is not None:
            if named_tok in named:
                found_placeholder = True
                return named[named_tok]
            # An undeclared ``$word`` is not a placeholder — leave it verbatim
            # so skill bodies containing literal ``$`` text are unharmed.
            return m.group(0)
        pos_tok = m.group("pos")
        if pos_tok is not None:
            found_placeholder = True
            n = int(pos_tok)
            return positionals[n - 1] if 1 <= n <= len(positionals) else ""
        return m.group(0)

    result = _TOKEN_RE.sub(repl, body)

    if not found_placeholder and raw_args.strip():
        result = f"{result}\n\nARGUMENTS: {raw_args.strip()}"
    return result
