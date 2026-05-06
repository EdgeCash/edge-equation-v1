"""
BRAND_GUIDE quality gates — extracted from scrapers'
exporters/mlb/daily_spreadsheet.py and made into its own module so v1
engines (and any other sport's exporter we later port) can import the
same gating contract.

Two-tier gating:

1. PER-MARKET GATE (`market_gate`): a market type (moneyline, run_line,
   totals, first_5, first_inning, team_totals) only ships ANY pick on a
   given day if the rolling backtest summary for that bet_type clears
   ALL of:
       - n_bets >= MIN_GATE_BETS  (default 200)
       - roi_pct >= MIN_GATE_ROI  (default +1.0)
       - brier   <  MAX_GATE_BRIER (default 0.246)

   When the backtest hasn't accumulated MIN_GATE_BETS yet (cold start),
   the gate returns None and the caller treats every market as
   provisionally allowed.

2. PER-PICK EDGE FLOOR (`edge_floor_for`): the pick itself must clear
   a market-specific edge percentage. Defaults reflect each market's
   typical sharpness — totals/F5 are sharper than ML, so they get
   lower floors. CLI flags can override per market.

The two gates are AND-ed. A market that clears (1) still has to publish
only picks that clear (2).

This module is the single source of truth for the gate constants. Both
the daily orchestrator and any backtest replay must import from here.
"""
from __future__ import annotations

from typing import Iterable


MIN_GATE_BETS: int = 200
MIN_GATE_ROI: float = 1.0          # percent
MAX_GATE_BRIER: float = 0.246


DEFAULT_EDGE_THRESHOLDS_BY_MARKET: dict[str, float] = {
    "moneyline":     4.0,
    "run_line":      3.0,
    "totals":        2.5,
    "first_5":       2.5,
    "first_inning":  4.0,
    "team_totals":   3.0,
}


def market_gate(
    backtest_summary: Iterable[dict] | None,
    min_bets: int = MIN_GATE_BETS,
    min_roi: float = MIN_GATE_ROI,
    max_brier: float = MAX_GATE_BRIER,
) -> tuple[set[str] | None, dict[str, str]]:
    """Apply the rolling-window market gate.

    Args:
        backtest_summary: iterable of rows shaped
            {"bet_type": str, "bets": int, "roi_pct": float, "brier": float}.
            Pass None or empty for cold-start (no history yet).
        min_bets / min_roi / max_brier: thresholds — see module docstring.

    Returns:
        (passed_set, notes_dict). passed_set is None when there is no
        backtest data yet. notes_dict maps each excluded bet_type to a
        short reason string for logging / display.
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
    """Per-market edge floor in percent. Returns the override if present,
    else the default. Unknown markets default to 3.0% (conservative)."""
    if overrides and bet_type in overrides:
        return overrides[bet_type]
    return DEFAULT_EDGE_THRESHOLDS_BY_MARKET.get(bet_type, 3.0)


FLAT_DECIMAL_ODDS = 1.909  # -110, the assumed historical price


def prob_floor_for(
    bet_type: str,
    decimal_odds: float = FLAT_DECIMAL_ODDS,
    overrides: dict[str, float] | None = None,
) -> float:
    """Translate a per-market edge floor (in %) into a model-probability
    floor at the assumed flat price. At -110 (decimal 1.909), edge_pct =
    prob * 1.909 - 1 ; so prob_floor = (1 + edge/100) / decimal_odds.

    This is what the play-only gate uses to decide which historical bets
    "would have been published" — i.e., simulate the production filter
    against a flat-price backfill where we don't have historical odds.
    """
    edge_floor_pct = edge_floor_for(bet_type, overrides=overrides)
    return (1.0 + edge_floor_pct / 100.0) / decimal_odds


def select_summary_for_gate(
    backtest_payload: dict | None,
) -> tuple[list[dict] | None, str]:
    """Pick the right summary slice to feed `market_gate`.

    The original gate consumed `summary_by_bet_type`, which scores every
    line the model touched (e.g. 1752 totals = 3 lines x 584 games). That
    over-states sample size and dilutes both ROI and Brier with marginal
    rows the production filter would have dropped. When the backtest
    provides `summary_by_bet_type_play_only`, prefer it — it represents
    the slice we'd actually publish.

    Returns (summary, source_label). source_label is for logging only.
    """
    if not backtest_payload:
        return None, "none"
    play = backtest_payload.get("summary_by_bet_type_play_only")
    if play:
        return play, "play_only"
    return backtest_payload.get("summary_by_bet_type"), "all"


def parse_threshold_overrides(args: list[str] | None) -> dict[str, float]:
    """Parse `--edge-threshold MARKET=PCT` repeated CLI flags into a dict.
    Skips malformed entries silently rather than aborting the run."""
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
