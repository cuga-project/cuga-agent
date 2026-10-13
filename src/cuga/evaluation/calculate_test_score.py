from typing import Tuple, Dict, Any
from collections import Counter
from difflib import SequenceMatcher
from pydantic import BaseModel
from enum import Enum
import json
from typing import Optional, List
import re
from rapidfuzz import fuzz, distance
from cuga.backend.tools_env.registry.mcp_manager.adapter import sanitize_tool_name


class ToolCall(BaseModel):
    """
    Basic model for a tool call
    """

    name: str
    args: Dict


class ScoringMethod(str, Enum):
    EXACT = "exact"
    SEQUENCE_MATCHER = "sequence_matcher"
    JACCARD = "jaccard"
    COSINE = "cosine"
    FUZZY_PARTIAL = "fuzzy_partial"
    FUZZY_TOKEN_SET = "fuzzy_token_set"
    JARO_WINKLER = "jaro_winkler"
    LEVENSHTEIN_NORM = "levenshtein_norm"


class ToolCallMismatchType(str, Enum):
    ARGS_MISMATCH = "args_mismatch"
    NAME_MISMATCH = "name_mismatch"
    MISSING = "missing"
    UNEXPECTED = "unexpected"


class ToolCallMismatch(BaseModel):
    tool_name: str
    type: ToolCallMismatchType
    expected: Optional[ToolCall] = None
    actual: Optional[ToolCall] = None


class TestScore(BaseModel):
    """
    Basic model for test score
    """

    keyword_score: float
    tool_call_score: float
    response_score: float
    response_scoring_type: ScoringMethod
    # Populated post-hoc by evaluate_cuga.py when run with --agentic-quality
    # (needs the tool catalog + judge model, which this module doesn't have).
    # None means the judge wasn't run for this test case, not that it scored zero.
    tool_selection_quality: Optional[Dict[str, Any]] = None
    action_advancement: Optional[Dict[str, Any]] = None


class TestScoreDetails(BaseModel):
    """
    Detailed artifacts to inspect why a test scored the way it did.
    """

    missing_keywords: List[str]
    expected_keywords: List[str]
    expected_tool_calls: List[ToolCall]
    tool_call_mismatches: List[ToolCallMismatch]
    response_expected: str
    response_actual: str
    response_scoring_type: ScoringMethod


def _normalize_tokens(s: str) -> List[str]:
    return [t for t in "".join(ch.lower() if ch.isalnum() else " " for ch in s).split() if t]


def _jaccard(a_tokens: List[str], b_tokens: List[str]) -> float:
    a, b = set(a_tokens), set(b_tokens)
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


def _cosine_tf(a_tokens: List[str], b_tokens: List[str]) -> float:
    if not a_tokens and not b_tokens:
        return 1.0
    from collections import Counter as C

    ca, cb = C(a_tokens), C(b_tokens)
    dot = sum(ca[k] * cb.get(k, 0) for k in ca)
    na = sum(v * v for v in ca.values()) ** 0.5
    nb = sum(v * v for v in cb.values()) ** 0.5
    return (dot / (na * nb)) if na and nb else 0.0


def _sequence_matcher(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# ========== 1) Keyword scoring ==========
def score_keywords(answer: str, expected_keywords: List[str]) -> Tuple[float, List[str]]:
    """
    Calculate how many expected keywords appear in the given text.
    Matching is case-insensitive and ignores punctuation/formatting.
    Returns: (score, missing_keywords)
    """
    if not expected_keywords:
        return 1.0, []

    # Normalize the text: lowercase + remove punctuation
    normalized_text = re.sub(r"[^a-z0-9]+", " ", answer.lower())

    missing_keywords = []
    for kw in expected_keywords:
        normalized_kw = re.sub(r"[^a-z0-9]+", " ", kw.lower()).strip()
        if normalized_kw not in normalized_text:
            missing_keywords.append(kw)

    found = len(expected_keywords) - len(missing_keywords)
    score = found / len(expected_keywords)

    return round(score, 4), missing_keywords


# ========== 2) Response proximity ==========
def score_response(
    actual: str, expected: str, method: ScoringMethod = ScoringMethod.SEQUENCE_MATCHER
) -> Tuple[float, ScoringMethod]:
    if method == ScoringMethod.EXACT:
        return (1.0 if actual == expected else 0.0), ScoringMethod.EXACT

    if method == ScoringMethod.SEQUENCE_MATCHER:
        return _sequence_matcher(actual, expected), ScoringMethod.SEQUENCE_MATCHER

    if method in {ScoringMethod.JACCARD, ScoringMethod.COSINE}:
        toks_a, toks_b = _normalize_tokens(actual), _normalize_tokens(expected)
        if method == ScoringMethod.JACCARD:
            return _jaccard(toks_a, toks_b), ScoringMethod.JACCARD
        return _cosine_tf(toks_a, toks_b), ScoringMethod.COSINE

    if method == ScoringMethod.FUZZY_PARTIAL:
        # robust to extra prefixes/suffixes; good for “expected snippet within longer response”
        return round(fuzz.partial_ratio(expected, actual) / 100.0, 4), ScoringMethod.FUZZY_PARTIAL

    if method == ScoringMethod.FUZZY_TOKEN_SET:
        # ignores word order and duplicates—great for rephrased responses
        return round(fuzz.token_set_ratio(expected, actual) / 100.0, 4), ScoringMethod.FUZZY_TOKEN_SET

    if method == ScoringMethod.JARO_WINKLER:
        # typo-friendly; higher for small transpositions; normalized 0..1
        jw = distance.JaroWinkler.normalized_similarity(expected, actual)
        return round(float(jw), 4), ScoringMethod.JARO_WINKLER

    if method == ScoringMethod.LEVENSHTEIN_NORM:
        # classic edit distance normalized to 0..1 similarity
        sim = distance.Levenshtein.normalized_similarity(expected, actual)
        return round(float(sim), 4), ScoringMethod.LEVENSHTEIN_NORM

    # default
    return _sequence_matcher(actual, expected), ScoringMethod.SEQUENCE_MATCHER


def _canon_args(d: Dict[str, Any]) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _canon(tc: ToolCall) -> Tuple[str, str]:
    return (tc.name, _canon_args(tc.args))


def _key(tc: ToolCall) -> Tuple[str, str]:
    """Canonical key (name + normalized args) for comparing tool calls."""
    return _canon(tc)


def score_tool_calls_exact(
    actual: List[ToolCall],
    expected: List[ToolCall],
) -> Tuple[float, List[ToolCallMismatch]]:
    """
    Exact multiset match of (name, args).

    Scoring:
      matched = sum over keys of min(count_actual, count_expected)
      unexpected_count = sum((c_act - c_exp).values())   # extras in actual
      expected_count   = len(expected)
      score = 1.0 if (expected_count == 0 and unexpected_count == 0) else matched / (expected_count + unexpected_count)

    Mismatches (typed):
      - ARGS_MISMATCH (same tool name, different args)
      - NAME_MISMATCH (different tool used instead of expected)
      - MISSING       (expected not called)
      - UNEXPECTED    (called but not expected)
    """
    # sanitize tool names
    for tool_call in expected:
        tool_call.name = sanitize_tool_name(tool_call.name)
    exp_keys = [_key(tc) for tc in expected]
    act_keys = [_key(tc) for tc in actual]
    c_exp, c_act = Counter(exp_keys), Counter(act_keys)

    matched = sum(min(c_exp[k], c_act.get(k, 0)) for k in c_exp)
    unexpected_count = sum((c_act - c_exp).values())
    expected_count = len(expected)

    if expected_count == 0 and unexpected_count == 0:
        score = 1.0
    else:
        denom = expected_count + unexpected_count
        score = (matched / denom) if denom else 1.0

    # Build unmatched lists for detailed mismatch reporting
    def expand_unmatched(
        counter_a: Counter, counter_b: Counter, source_list: List[ToolCall]
    ) -> List[ToolCall]:
        leftover = counter_a - counter_b
        need: List[ToolCall] = []
        by_key: Dict[Tuple[str, str], List[ToolCall]] = {}
        for tc in source_list:
            by_key.setdefault(_key(tc), []).append(tc)
        for k, cnt in leftover.items():
            pool = by_key.get(k, [])
            need.extend(pool[:cnt])
        return need

    unmatched_expected = expand_unmatched(c_exp, c_act, expected)
    unmatched_actual = expand_unmatched(c_act, c_exp, actual)

    mismatches: List[ToolCallMismatch] = []

    # 1) Flag args mismatches first (same tool name, different args)
    #    Greedy, deterministic (left-to-right).
    used_a = set()
    still_ue: List[ToolCall] = []
    for e in unmatched_expected:
        found_ai = None
        for ai, a in enumerate(unmatched_actual):
            if ai in used_a:
                continue
            if a.name == e.name and a.args != e.args:
                mismatches.append(
                    ToolCallMismatch(
                        tool_name=e.name,
                        type=ToolCallMismatchType.ARGS_MISMATCH,
                        expected=e.model_dump(),
                        actual=a.model_dump(),
                    )
                )
                used_a.add(ai)
                found_ai = ai
                break
        if found_ai is None:
            still_ue.append(e)

    still_ua: List[ToolCall] = [a for ai, a in enumerate(unmatched_actual) if ai not in used_a]
    unmatched_expected, unmatched_actual = still_ue, still_ua

    # 2) Pair remaining as name mismatches (A instead of B)
    #    Pair in order to stay deterministic.
    for e, a in zip(unmatched_expected, unmatched_actual):
        mismatches.append(
            ToolCallMismatch(
                tool_name=e.name,  # expected tool name
                type=ToolCallMismatchType.NAME_MISMATCH,
                expected=e.model_dump(),
                actual=a.model_dump(),
            )
        )

    # 3) Any leftovers after pairing are pure missing/unexpected
    if len(unmatched_expected) > len(unmatched_actual):
        for e in unmatched_expected[len(unmatched_actual) :]:
            mismatches.append(
                ToolCallMismatch(
                    tool_name=e.name,
                    type=ToolCallMismatchType.MISSING,
                    expected=e.model_dump(),
                    actual=None,
                )
            )
    elif len(unmatched_actual) > len(unmatched_expected):
        for a in unmatched_actual[len(unmatched_expected) :]:
            mismatches.append(
                ToolCallMismatch(
                    tool_name=a.name,
                    type=ToolCallMismatchType.UNEXPECTED,
                    expected=None,
                    actual=a.model_dump(),
                )
            )

    return round(score, 4), mismatches


# ========== Orchestrators ==========
def evaluate_test(
    expected_keywords: List[str],
    tool_calls: List[ToolCall],
    expected_tool_calls: List[ToolCall],
    response: str,
    expected_response: str,
    response_scoring_type: ScoringMethod = ScoringMethod.FUZZY_TOKEN_SET,
) -> TestScore:
    """
    Backward-compatible: returns only TestScore.
    """
    kw_score, _missing_keywords = score_keywords(response, expected_keywords)
    tc_score, _tc_mismatches = score_tool_calls_exact(tool_calls, expected_tool_calls)
    resp_score, resp_method = score_response(response, expected_response, method=response_scoring_type)

    return TestScore(
        keyword_score=round(kw_score, 4),
        tool_call_score=round(tc_score, 4),
        response_score=round(resp_score, 4),
        response_scoring_type=resp_method,
    )


def evaluate_test_and_details(
    expected_keywords: List[str],
    tool_calls: List[ToolCall],
    expected_tool_calls: List[ToolCall],
    response: str,
    expected_response: str,
    response_scoring_type: ScoringMethod = ScoringMethod.FUZZY_TOKEN_SET,
) -> Tuple[TestScore, TestScoreDetails]:
    kw_score, missing_keywords = score_keywords(response, expected_keywords)
    tc_score, tc_mismatches = score_tool_calls_exact(tool_calls, expected_tool_calls)
    resp_score, resp_method = score_response(response, expected_response, method=response_scoring_type)

    score = TestScore(
        keyword_score=round(kw_score, 4),
        tool_call_score=round(tc_score, 4),
        response_score=round(resp_score, 4),
        response_scoring_type=resp_method,
    )
    details = TestScoreDetails(
        expected_keywords=expected_keywords,
        missing_keywords=missing_keywords,
        expected_tool_calls=expected_tool_calls,
        tool_call_mismatches=tc_mismatches,
        response_expected=expected_response,
        response_actual=response,
        response_scoring_type=resp_method,
    )
    return score, details
