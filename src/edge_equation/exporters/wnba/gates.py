"""
WNBA quality gates — same contract as MLB but with WNBA-tuned defaults.

Two-tier gate:

1. PER-MARKET GATE (`market_gate`): a market type (moneyline,
   spread, totals) only ships picks if its rolling backtest summary
   clears n_bets >= MIN_GATE_BETS, roi_pct >= MIN_GATE_ROI, AND
   brier < MAX_GATE_BRIER.

   When the backtest hasn't accumulated MIN_GATE_BETS yet (early
   season cold-start), the gate returns None and the caller treats
   every market as provisionally allowed. This is the cold-start
   safety margin we promised for opening day — until we have ~200
   current-season bets, every pick that clears the per-pick edge
   floor is eligible.

2. PER-PICK EDGE FLOOR (`edge_floor_for`): the pick itself must
   clear a market-specific edge percentage.

Default thresholds are slightly different from MLB:
  - Brier ceiling: 0.250 (was 0.246 for MLB). Basketball totals
    are inherently noisier than MLB run totals; tighter calibration
    + bigger spreads.
  - ROI floor: same +1%
  - Bet count floor: same 200
  - Edge floors: tighter on totals (basketball totals markets are
    the sharpest) and looser on moneyline (heavier juice on heavy
    favorites).
"""
from __future__ import annotations

from typing import Iterable


MIN_GATE_BETS: int = 200
MIN_GATE_ROI: float = 1.0          # percent
MAX_GATE_BRIER: float = 0.250      # was 0.246 for MLB; basketball is noisier


DEFAULT_EDGE_THRESHOLDS_BY_MARKET: dict[str, float] = {
    "moneyline": 4.0,
    "spread":    3.0,
    "totals":    2.5,
}


def market_gate(
    backtest_summary: Iterable[dict] | None,
    min_bets: int = MIN_GATE_BETS,
    min_roi: float = MIN_GATE_ROI,
    max_brier: float = MAX_GATE_BRIER,
) -> tuple[set[str] | None, dict[str, str]]:
    """Return (passed_set, notes_dict). passed_set is None for
    cold-start (no backtest data yet); the caller treats None as
    'every market allowed'.
    """
    if not backtest_summary:
        return None, {}

    passed: set[str] = set()
    notes: dict[str, str] = {}
    for row in backtest_summary:
        bt = row.get("bet_type")
        if not bt:
            continue
        bets = row.get("bets", 0) or 0
        roi = row.get("roi_pct") if row.get("roi_pct") is not None else 0.0
        brier = row.get("brier")

        if bets < min_bets:
            notes[bt] = f"sample {bets}<{min_bets}"
            continue
        if roi < min_roi:
            notes[bt] = f"ROI {roi:+.2f}%"
            continue
        if brier is None or brier >= max_brier:
            brier_str = f"{brier:.4f}" if brier is not None else "n/a"
            notes[bt] = f"Brier {brier_str}"
            continue
        passed.add(bt)
    return passed, notes


def edge_floor_for(
    bet_type: str,
    overrides: dict[str, float] | None = None,
) -> float:
    if overrides and bet_type in overrides:
        return overrides[bet_type]
    return DEFAULT_EDGE_THRESHOLDS_BY_MARKET.get(bet_type, 3.0)


def parse_threshold_overrides(args: list[str] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in args or []:
        if "=" not in raw:
            continue
        market, _, pct = raw.partition("=")
        market = market.strip().lower()
        try:
            out[market] = float(pct.strip())
        except ValueError:
            continue
    return out
