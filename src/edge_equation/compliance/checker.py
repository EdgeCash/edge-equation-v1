"""
compliance_test: final gate before any public-mode payload goes out.

Facts Not Feelings. No gambling language in free X content. Ever.

Checks a text payload (rendered post) or a card dict (pre-render) for:

  - Forbidden betting / tout language (bet, wager, gamble, pick, lock,
    smash, lotto, play, value, sharp, signal, sportsbook, parlay, and
    friends). Matched as whole words, case-insensitively.
  - Un-sanitized leaks from the sanitizer (edge percentages, Kelly
    numbers).
  - Presence of the mandatory Phase-1 disclaimer.
  - Presence of the mandatory Season Ledger footer (Phase 20).

The mandatory footer contains the word "bet" ("Bet within your means"),
which is part of a standard responsible-gambling PSA and must be
preserved verbatim. The scanner strips the whitelisted disclaimer text
and any matching ledger-footer pattern BEFORE searching for forbidden
terms, so those specific strings survive while every other "bet"
reference still trips the checker.

Returns a ComplianceReport with (ok: bool, violations: list[str]). The
publisher layer refuses to fire when ok is False.
"""
from dataclasses import dataclass, field
from typing import Iterable, List, Union
import re

from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
from edge_equation.compliance.sanitizer import PublicModeSanitizer


# Whole-word forbidden terms. Phase 20 tightens the list per the brand
# spec: no "smashes, locks, lottos, picks, bets, plays, value, sharp,
# signals" -- plus the classic gambling-jargon set.
FORBIDDEN_TERMS = (
    # Core gambling verbs / nouns
    "bet",
    "bets",
    "betting",
    "wager",
    "wagers",
    "wagering",
    "gamble",
    "gambling",
    "gambler",
    # Tout / hype language
    "pick",
    "picks",
    "lock",
    "locks",
    "smash",
    "smashes",
    "lotto",
    "lottos",
    "play",
    "plays",
    # Sharp / value / signal tout slang (Phase 20)
    "sharp",
    "sharps",
    "signal",
    "signals",
    # "value" is overloaded in English but banned per brand spec for free
    # X content.
    "value",
    # Sportsbook mechanics
    "sportsbook",
    "sportsbooks",
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


# Matches the dynamic part of the Season Ledger footer:
#   "Season Ledger: W-L-T +N.NN units +R.R ROI | Bet within your means.
#    Problem? Call 1-800-GAMBLER."
# Phase 20 made this footer mandatory on every free post, so we whitelist
# its exact shape (numbers-only in the W-L-T / units / ROI positions) when
# scanning for forbidden terms.
LEDGER_FOOTER_RE = re.compile(
    r"Season Ledger:\s*\d+-\d+-\d+\s+[+-]?\d+\.\d+\s+units\s+[+-]?\d+\.\d+\s+ROI"
    r"\s*\|\s*Bet within your means\.\s*Problem\?\s*Call 1-800-GAMBLER\.",
    re.IGNORECASE,
)


# The mission-statement disclaimer also contains whitelisted text. The
# current DISCLAIMER_TEXT doesn't carry any forbidden terms, but we strip
# it anyway so future edits that do include one are safe by construction.


# Player Prop Projections section: the user-facing column header row
# contains the word "Value" ("Player | Market | Projected Value | Grade
# | Key Read") as a structural table label, not a tout phrase. Strip
# the exact header before scanning so it doesn't false-positive the
# forbidden-terms check. Tout uses of "value" elsewhere in a post still
# trip the check.
PLAYER_PROP_HEADER_RE = re.compile(
    r"Player\s*\|\s*Market\s*\|\s*Projected Value\s*\|\s*Grade\s*\|\s*Key Read",
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


def _strip_whitelisted(text: str) -> str:
    """Remove the mandatory disclaimer + ledger-footer text + the
    Player Prop Projections table header so their internal whitelisted
    words ("Bet within your means", "Projected Value") don't trip the
    forbidden-terms scanner. All three whitelist rules are pattern-
    anchored so a tout use of the same words elsewhere still fails."""
    if not text:
        return text or ""
    out = text
    # Ledger footer is dynamic -> regex strip. Disclaimer is literal.
    out = LEDGER_FOOTER_RE.sub("", out)
    out = PLAYER_PROP_HEADER_RE.sub("", out)
    if DISCLAIMER_TEXT in out:
        out = out.replace(DISCLAIMER_TEXT, "")
    return out


def compliance_test(
    output: Union[str, dict],
    require_disclaimer: bool = True,
    require_ledger_footer: bool = False,
) -> ComplianceReport:
    """
    Validate a public-mode payload. Accepts either:
    - a rendered text post (str)
    - a card dict from PostingFormatter.build_card
    Returns a ComplianceReport; if report.ok is False, do NOT publish.

    require_disclaimer defaults True (mission-statement disclaimer).
    require_ledger_footer defaults False; flip on for any free-content
    post that must carry the Season Ledger footer per Phase 20 brand spec.
    """
    strings: List[str]
    if isinstance(output, str):
        strings = [output]
    elif isinstance(output, dict):
        strings = list(_collect_strings(output))
    else:
        return ComplianceReport(ok=False, violations=[f"unsupported output type: {type(output).__name__}"])

    violations: List[str] = []
    joined_original = "\n".join(strings)
    strings_for_scan = [_strip_whitelisted(s) for s in strings]

    # 1. Forbidden betting / tout language in any non-whitelisted surface.
    seen_terms = set()
    for s in strings_for_scan:
        for match in _FORBIDDEN_RE.findall(s):
            key = match.lower()
            if key not in seen_terms:
                seen_terms.add(key)
                violations.append(f"forbidden term: {match!r}")

    # 2. Sanitizer leaks (edge percentages, Kelly references).
    for s in strings_for_scan:
        leaks = PublicModeSanitizer.list_leaks(s)
        for leak in leaks:
            violations.append(f"sanitizer leak: {leak!r}")

    # 3. Mandatory disclaimer.
    if require_disclaimer and DISCLAIMER_TEXT not in joined_original:
        violations.append("missing mandatory disclaimer")

    # 4. Mandatory Season Ledger footer for free-content posts.
    if require_ledger_footer and not LEDGER_FOOTER_RE.search(joined_original):
        violations.append("missing mandatory Season Ledger footer")

    return ComplianceReport(ok=(len(violations) == 0), violations=violations)
