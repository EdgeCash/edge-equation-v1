"""MLB-only namespace — strict parlay engines + unified daily runner.

This package finalizes the MLB prediction stack. It does NOT define new
markets; every projection still flows through the existing per-market
engines (`engines.nrfi`, `engines.full_game`, `engines.props_prizepicks`).
What lives here:

* ``game_results_parlay``  — strict 3–6 leg parlay engine over the
                              game-level markets (ML / Run Line / Totals
                              / Team Totals / F5 / NRFI-YRFI).
* ``player_props_parlay``  — strict 3–6 leg parlay engine over MLB
                              player props (Hits, RBI, HR, K, Total
                              Bases, Runs, Stolen Bases, …).
* ``run_daily``            — unified MLB daily runner; produces one
                              card set covering NRFI/YRFI, full-game,
                              player props, and both parlays.
* ``backtest_parlays``     — walk-forward 2023–2025 backtest for the
                              two parlay engines (CLV, Brier, ROI,
                              calibration) on top of each engine's
                              existing per-market backtest.
* ``thresholds``           — single source of truth for the
                              "Facts. Not Feelings." parlay rules.

Every engine here reuses the shared `engines.parlay` builder, the
shared `engines.tiering` ladder, and the existing per-engine math /
context / persistence layers exactly like NRFI does. No premium / auth /
SaaS features; no other sports.
"""

from __future__ import annotations

from .thresholds import (
    MLB_PARLAY_RULES,
    MLBParlayRules,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
    NO_QUALIFIED_PARLAY_MESSAGE,
)
from .game_results_parlay import (
    MLBGameResultsParlayEngine,
    build_game_results_parlay,
    build_game_results_legs,
)
from .player_props_parlay import (
    MLBPlayerPropsParlayEngine,
    build_player_props_parlay,
    build_player_props_legs,
)
from .run_daily import MLBDailyRunner, build_unified_mlb_card

__all__ = [
    "MLB_PARLAY_RULES",
    "MLBParlayRules",
    "PARLAY_CARD_NOTE",
    "PARLAY_TRANSPARENCY_NOTE",
    "NO_QUALIFIED_PARLAY_MESSAGE",
    "MLBGameResultsParlayEngine",
    "MLBPlayerPropsParlayEngine",
    "MLBDailyRunner",
    "build_game_results_parlay",
    "build_game_results_legs",
    "build_player_props_parlay",
    "build_player_props_legs",
    "build_unified_mlb_card",
]
