"""
Tier-based Kelly sizing helper, ported verbatim from
edge-equation-scrapers/exporters/mlb/kelly.py (commit on main as of
2026-05-04). Produces the kelly_advice tier strings ("PASS" / "0.5u" /
"1u" / "2u" / "3u") that populate picks_log.json and the workbook's
Kelly column.

This is intentionally a leaf module — no v1-internal imports — so the
porting is byte-for-byte and any future scrapers refresh is a clean
diff.
"""
from __future__ import annotations

DEFAULT_DECIMAL_ODDS = 1.909   # -110 standard juice
HALF_KELLY = 0.5
MAX_KELLY_FRACTION = 0.05      # 5% bankroll cap


def american_to_decimal(american: int | float) -> float:
    am = float(american)
    if am > 0:
        return round(1 + am / 100, 4)
    if am < 0:
        return round(1 + 100 / -am, 4)
    return 1.0


def decimal_to_american(decimal: float) -> int:
    if decimal is None or decimal <= 1.0:
        return 0
    if decimal >= 2.0:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def kelly_fraction(prob: float, decimal_odds: float = DEFAULT_DECIMAL_ODDS) -> float:
    b = decimal_odds - 1
    if b <= 0 or prob <= 0 or prob >= 1:
        return 0.0
    f = (b * prob - (1 - prob)) / b
    return max(0.0, f)


def _tier(fraction: float) -> str:
    if fraction <= 0.005:
        return "PASS"
    if fraction <= 0.015:
        return "0.5u"
    if fraction <= 0.030:
        return "1u"
    if fraction <= 0.050:
        return "2u"
    return "3u"


def tier_from_pct(kelly_pct: float | None) -> str:
    if kelly_pct is None:
        return "PASS"
    return _tier(max(0.0, kelly_pct) / 100.0)


def kelly_advice(
    prob: float,
    decimal_odds: float = DEFAULT_DECIMAL_ODDS,
    fraction_of_kelly: float = HALF_KELLY,
    cap: float = MAX_KELLY_FRACTION,
) -> dict:
    full = kelly_fraction(prob, decimal_odds)
    sized = min(full * fraction_of_kelly, cap)
    return {
        "model_prob": round(prob, 3),
        "fair_odds_dec": round(1 / prob, 3) if 0 < prob < 1 else None,
        "decimal_odds_used": decimal_odds,
        "kelly_full_pct": round(full * 100, 2),
        "kelly_pct": round(sized * 100, 2),
        "kelly_advice": _tier(sized),
    }


def edge_pct(prob: float, decimal_odds: float | None) -> float | None:
    if decimal_odds is None or decimal_odds <= 1.0 or not (0 < prob < 1):
        return None
    return round((prob - 1.0 / decimal_odds) * 100, 2)
