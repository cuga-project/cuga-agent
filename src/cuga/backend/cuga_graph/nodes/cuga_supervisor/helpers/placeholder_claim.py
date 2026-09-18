"""Conservative recognition of unfinished result claims in playbook replies."""

import re

_PLACEHOLDER = r"(?<!\{)\{[A-Za-z_]\w*\}(?!\})"
_RESULT_CLAIM = re.compile(
    r"\b(?:is|are|was|were)\s+[*`_]*" + _PLACEHOLDER + r"|\b[\w ]+:\s*[*`_]*" + _PLACEHOLDER,
    re.IGNORECASE,
)
_INPUT_OR_BLOCKER = re.compile(
    r"\?|\b(?:need|needs|needed|require|requires|required|missing|awaiting|waiting|"
    r"cannot|unable|blocked|template)\b|\bcan['’]t\b|"
    r"\b(?:please|could you|would you|can you)\b",
    re.IGNORECASE,
)


def has_unresolved_result_claim(content: str) -> bool:
    """Retry explicit placeholder-valued results, never apparent input requests.

    This is deliberately narrower than natural-language intent classification:
    ambiguous replies finish normally. A blocker anywhere in the reply wins
    over a result claim, including partially completed tasks needing consent.
    """
    return bool(_RESULT_CLAIM.search(content)) and not _INPUT_OR_BLOCKER.search(content)
