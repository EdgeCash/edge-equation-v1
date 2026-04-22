"""
compliance_test: final gate before any public-mode payload goes out.

Checks a text payload (rendered post) or a card dict (pre-render) for:

  - Forbidden betting language (bet, wager, gamble, pick, lock, sportsbook,
    book, parlay, units, juice, etc). Matched as whole words,
    case-insensitively.
  - Un-sanitized leaks from the sanitizer (edge percentages, Kelly numbers).
  - Presence of the mandatory Phase-1 disclaimer.

Returns a ComplianceReport with (ok: bool, violations: list[str]). The
publisher layer refuses to fire when ok is False.
"""
from dataclasses import dataclass, field
from typing import Iterable, List, Union
import re

from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
from edge_equation.compliance.sanitizer import PublicModeSanitizer


# Whole-word forbidden terms. Keep this deliberately broad -- a false
# positive aborts a post, a false negative slips a ToS violation onto X.
FORBIDDEN_TERMS = (
    "bet",
    "bets",
    "betting",
    "wager",
    "wagers",
    "wagering",
    "gamble",
    "gambling",
    "gambler",
    "pick",
    "picks",
    "lock",
    "locks",
    "sportsbook",
    "parlay",
    "parlays",
    "units",
    "juice",
    "vig",
    "handicap",
    "handicapper",
)


_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in FORBIDDEN_TERMS) + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ComplianceReport:
    ok: bool
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": list(self.violations)}


def _collect_strings(obj: Union[str, dict, list, None]) -> Iterable[str]:
    """Walk a dict/list structure yielding every string leaf for scanning."""
    if obj is None:
        return []
    if isinstance(obj, str):
        return [obj]
    out: List[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_strings(v))
    return out


def compliance_test(
    output: Union[str, dict],
    require_disclaimer: bool = True,
) -> ComplianceReport:
    """
    Validate a public-mode payload. Accepts either:
    - a rendered text post (str)
    - a card dict from PostingFormatter.build_card
    Returns a ComplianceReport; if report.ok is False, do NOT publish.

    Set require_disclaimer=False to skip the mandatory-disclaimer check when
    validating non-publication surfaces (e.g. internal admin dashboards).
    """
    strings: List[str]
    if isinstance(output, str):
        strings = [output]
    elif isinstance(output, dict):
        strings = list(_collect_strings(output))
    else:
        return ComplianceReport(ok=False, violations=[f"unsupported output type: {type(output).__name__}"])

    violations: List[str] = []

    # 1. Forbidden betting language anywhere in the payload.
    seen_terms = set()
    for s in strings:
        for match in _FORBIDDEN_RE.findall(s):
            key = match.lower()
            if key not in seen_terms:
                seen_terms.add(key)
                violations.append(f"forbidden term: {match!r}")

    # 2. Sanitizer leaks (edge percentages, Kelly references).
    for s in strings:
        leaks = PublicModeSanitizer.list_leaks(s)
        for leak in leaks:
            violations.append(f"sanitizer leak: {leak!r}")

    # 3. Mandatory disclaimer. For dict payloads, the tagline field is the
    #    canonical home; fall back to any string match across the payload.
    if require_disclaimer:
        joined = "\n".join(strings)
        if DISCLAIMER_TEXT not in joined:
            violations.append("missing mandatory disclaimer")

    return ComplianceReport(ok=(len(violations) == 0), violations=violations)
