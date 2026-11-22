"""Keyword terms for knowledge search: one tokenizer for every language and storage backend.

The index and the query both go through :func:`terms`, so a keyword match never depends on a
database's own word rules, and every backend matches the same way. The rules:

- NFKC and case folding: full-width forms, ligatures and case variants match ("ＡＢＣ" is
  "abc", "Straße" is "strasse").
- Marks writers routinely omit are dropped: accents on Latin and Greek letters, Hebrew points,
  Arabic harakat and tatweel. Marks that spell a word are kept (Indic vowel signs and viramas,
  Thai vowels, Cyrillic "й"), so "हिन्दी" stays one word.
- A term is a run of letters, marks and digits; punctuation separates terms. A sentence-final
  "K5A6FF80A." is the term "k5a6ff80a", and "ABC-123" is the phrase "abc 123", so it also
  matches "ABC 123" and "abc_123".
- Chinese, Japanese, Korean, Thai, Lao, Khmer and Myanmar runs are indexed as overlapping
  character pairs, as in Lucene's CJK bigram filter: a word inside an unspaced sentence matches
  without a dictionary, and a Korean noun matches with its particle attached.

Terms longer than 255 characters (encoded blobs, not words anyone types) are dropped; Postgres
caps a lexeme at 2 KB.

Not handled: stemming and attached prefixes (Hebrew "ה", Arabic "ال", English plurals), and a
one-character query in the unspaced scripts. Bump ``VERSION`` whenever a rule changes: stored
indexes built by another version are rebuilt on first use.
"""

from __future__ import annotations

import unicodedata

import regex

VERSION = 1
_MAX_TERM_CHARS = 255

_TERM = regex.compile(r"[\p{L}\p{M}\p{N}]+")
_UNSPACED = regex.compile(
    r"[\p{scx=Han}\p{scx=Hiragana}\p{scx=Katakana}\p{scx=Hangul}"
    r"\p{scx=Thai}\p{scx=Lao}\p{scx=Khmer}\p{scx=Myanmar}]+"
)
_OPTIONAL_MARKS = regex.compile(
    r"(?<=[\p{Latin}\p{Greek}])[\u0300-\u036f]+"  # accents, after NFD
    r"|[\u0591-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5\u05c7]"  # Hebrew cantillation and points
    r"|[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4\u06e7\u06e8\u06ea-\u06ed\u0640]"  # Arabic
)


def _normalize(text: str) -> str:
    """NFKC, case folding, and removal of the optional marks."""
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text).casefold())
    return unicodedata.normalize("NFC", _OPTIONAL_MARKS.sub("", decomposed))


def _split_unspaced(word: str) -> list[str]:
    """Split a word's unspaced-script runs into overlapping character pairs."""
    out: list[str] = []
    pos = 0
    for run in _UNSPACED.finditer(word):
        if run.start() > pos:
            out.append(word[pos : run.start()])
        chars = run.group()
        out.extend(chars[i : i + 2] for i in range(max(1, len(chars) - 1)))
        pos = run.end()
    if pos < len(word):
        out.append(word[pos:])
    return out


def terms(text: str) -> list[str]:
    """The keyword terms of ``text``, in order."""
    return [
        term
        for word in _TERM.findall(_normalize(text))
        for term in _split_unspaced(word)
        if len(term) <= _MAX_TERM_CHARS
    ]


# Each group is one more match condition (and on Postgres one more count); a pasted paragraph of
# Chinese makes hundreds of character pairs. Repeated groups add nothing.
_MAX_QUERY_GROUPS = 64
_QUERY_WORD_BREAK = regex.compile(r"\s+")
# Inside a query word, punctuation other than these separates alternatives ("Paris/London"). These
# join one identifier's parts: "ABC-123", "user@example.com", "engine.py", "AT&T", and Hebrew
# acronyms written with a quote or gershayim ("צה״ל").
_SEPARATOR = regex.compile(r'[^\p{L}\p{M}\p{N}\p{Pd}_.@:&"\u05f3\u05f4]+')


def query_groups(query: str) -> list[list[str]]:
    """The query's terms in groups: a document matches if it has any group, with that group's
    terms adjacent and in order.

    A query word joined by other punctuation splits into its parts ("Paris/London", "IPv4/IPv6"),
    each one group; a part whose terms are joined by an identifier's punctuation ("ABC-123",
    "user@example.com", "Node.js") is matched as a phrase. A word whose parts are all single
    letters ("I/O", "A/B") stays one phrase: a one-letter group matches almost every document.
    For the same reason a one-letter part of a longer word is dropped: a possessive or a plural in
    parentheses ("MacDuff's", "office(s)") or an elided article ("l'homme"). An apostrophe
    separates parts, so "MacDuff's" finds "MacDuff". Character pairs from unspaced scripts are
    separate groups, so a sentence-long Chinese query ranks documents by how many pairs they share.
    Repeated groups are dropped and at most _MAX_QUERY_GROUPS are kept.
    """
    groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for word in _QUERY_WORD_BREAK.split(query):
        parts = [part for part in map(terms, _SEPARATOR.split(_normalize(word))) if part]
        word_terms = [term for part in parts for term in part]  # terms(word): punctuation is no term
        if any(_UNSPACED.match(t) for t in word_terms):
            candidates = [[t] for t in word_terms]
        elif len(parts) > 1 and all(len(part) == 1 and len(part[0]) == 1 for part in parts):
            candidates = [word_terms]
        else:
            candidates = [part for part in parts if len(part) > 1 or len(part[0]) > 1] or parts
        for group in candidates:
            if tuple(group) not in seen:
                seen.add(tuple(group))
                groups.append(group)
    return groups[:_MAX_QUERY_GROUPS]
