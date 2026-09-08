import re
from dataclasses import dataclass
from typing import Literal

VerifyGate = Literal["ok", "revise", "unknown"]

# Lives here (not in pre_execute) so response_utils can import it without the LLM chain.
VERIFY_BLOCKED_PREFIX = "VERIFY blocked this code block before execution."


@dataclass(frozen=True)
class VerifyDecision:
    gate: VerifyGate
    alert: str = ""
    raw: str = ""


# The gate line as models actually write it. Tolerates markdown around the label
# (**GATE**, - GATE, `GATE`), a space or fullwidth colon, bold around the value and
# trailing punctuation or a parenthetical. Before this, "GATE: revise." parsed as
# unknown -- and unknown runs the block, so any decoration silently turned the
# gate off. Anchored at line start so prose mentioning "gate" is not a verdict.
_GATE_RE = re.compile(r"^[\s\-*#>`]*GATE[\s*`]*[:：]\s*[*_`]*(ok|revise)\b", re.IGNORECASE)
_ALERT_RE = re.compile(r"^[\s\-*#>`]*ALERT[\s*`]*[:：]\s*(.*)$", re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")


def parse_verify_output(text: str) -> VerifyDecision:
    raw = (text or "").strip()
    if not raw:
        return VerifyDecision(gate="unknown")
    # Reasoning models emit scratch work first; a line in it that happens to start
    # with ALERT: is not the verdict's alert.
    body = _THINK_RE.sub("", raw)
    gate: VerifyGate = "unknown"
    alert_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _FENCE_RE.match(stripped):
            continue
        m = _GATE_RE.match(stripped)
        if m:
            gate = m.group(1).lower()  # type: ignore[assignment]
            continue
        m = _ALERT_RE.match(stripped)
        if m:
            rest = m.group(1).strip().strip("*_`").strip()
            if rest:
                alert_lines.append(rest)
            continue
        if gate == "revise":
            alert_lines.append(stripped)
    return VerifyDecision(gate=gate, alert="\n".join(alert_lines).strip(), raw=raw)
