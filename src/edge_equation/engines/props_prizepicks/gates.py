"""BRAND_GUIDE rolling-backtest gate for player props.

Mirrors `exporters/mlb/gates.py` so a single mental model covers both
sport surfaces:

  - When a backtest payload (or the live tier-ledger summary) has
    accumulated ≥200 graded bets per market, evaluate the +1% ROI /
    Brier <0.246 thresholds and return the set of markets allowed
    onto the daily card.
  - Cold-start (no data yet, or per-market sample below the floor)
    returns ``None`` -- the operator's daily orchestrator interprets
    this as "all markets allowed."

Two summary sources can feed this:

  1. ``exporters.mlb.props_backtest.PropsBacktestEngine.run()`` --
     historical replay produced from the per-game player backfill.
  2. The DuckDB ``props_tier_ledger`` rollup, once it has a useful
     sample of live settled outcomes.

Both shape rows the same way (``bet_type / bets / roi_pct / brier``)
so the gate doesn't care which one it sees.
"""
from __future__ import annotations

from typing import Iterable, Optional


MIN_GATE_BETS: int = 200
MIN_GATE_ROI: float = 1.0          # percent
MAX_GATE_BRIER: float = 0.246


# Per-market edge floor in percentage points. Lower than MLB game
# results because props at -110 typically clear at thinner margins
# (and the variance per pick is lower since stakes are smaller).
DEFAULT_PROPS_EDGE_THRESHOLDS: dict[str, float] = {
    "HR":          5.0,
    "Hits":        4.0,
    "Total_Bases": 4.0,
    "RBI":         4.0,
    "K":           4.0,
}


def edge_floor_for(
    bet_type: str,
    overrides: Optional[dict[str, float]] = None,
) -> float:
    """Per-market edge floor in percent."""
    if overrides and bet_type in overrides:
        return overrides[bet_type]
    return DEFAULT_PROPS_EDGE_THRESHOLDS.get(bet_type, 4.0)


def market_gate(
    backtest_summary: Optional[Iterable[dict]],
    min_bets: int = MIN_GATE_BETS,
    min_roi: float = MIN_GATE_ROI,
    max_brier: float = MAX_GATE_BRIER,
) -> tuple[Optional[set[str]], dict[str, str]]:
    """Apply the rolling-window market gate.

    Returns:
        (passed_set, notes_dict). ``passed_set`` is None when there is
        no data yet (cold start, all markets allowed). ``notes_dict``
        maps each excluded bet_type to a short reason string.
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


def select_summary_for_gate(
    backtest_payload: Optional[dict],
) -> tuple[Optional[list[dict]], str]:
    """Pick the right summary slice. Prefers play-only when present."""
    if not backtest_payload:
        return None, "none"
    play = backtest_payload.get("summary_by_bet_type_play_only")
    if play:
        return play, "play_only"
    return backtest_payload.get("summary_by_bet_type"), "all"
