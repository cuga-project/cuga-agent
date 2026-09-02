from dataclasses import dataclass
from typing import Literal

VerifyGate = Literal["ok", "revise", "unknown"]


@dataclass(frozen=True)
class VerifyDecision:
    gate: VerifyGate
    alert: str = ""
    raw: str = ""


def parse_verify_output(text: str) -> VerifyDecision:
    raw = (text or "").strip()
    if not raw:
        return VerifyDecision(gate="unknown")
    gate: VerifyGate = "unknown"
    alert_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("GATE:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value in ("ok", "revise"):
                gate = value  # type: ignore[assignment]
            continue
        if upper.startswith("ALERT:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                alert_lines.append(rest)
            continue
        if gate == "revise" and stripped:
            alert_lines.append(stripped)
    return VerifyDecision(gate=gate, alert="\n".join(alert_lines).strip(), raw=raw)
