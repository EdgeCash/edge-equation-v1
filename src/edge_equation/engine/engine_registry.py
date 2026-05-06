"""Global engine registry for Edge Equation.

Maps an engine key → (lazy) factory that returns the engine's runner.
Lazy because not every engine is importable in every environment
(NRFI's [nrfi] extras include xgboost / shap / pybaseball; the
unified MLB runner imports the optional Odds API client). Calling
``get_engine(...)`` is the only way an engine gets imported, so a
single missing extra doesn't take the whole registry down.

Register every engine the system can run here. Each value is a
zero-arg factory that returns an engine instance. Coverage:

MLB:
* ``mlb_nrfi``                — NRFI / YRFI engine.
* ``mlb_props``               — Player props (PrizePicks / books).
* ``mlb_fullgame``            — Full-game ML / RL / Total / Team
                                 Total / F5.
* ``mlb_game_results_parlay`` — Strict 3–6 leg game-results parlay.
* ``mlb_player_props_parlay`` — Strict 3–6 leg player-props parlay.
* ``mlb_daily``               — Unified MLB daily runner.

WNBA (feature-flagged off by default until opening-weekend testing
clears — set ``EDGE_FEATURE_WNBA_PARLAYS=on`` to enable the parlay
keys; the per-row ``wnba`` engine is always available):
* ``wnba``                     — Per-row WNBA engine (game + props).
* ``wnba_game_results_parlay`` — Strict 3–6 leg WNBA game parlay.
* ``wnba_player_props_parlay`` — Strict 3–6 leg WNBA props parlay.
* ``wnba_daily``               — Unified WNBA daily runner.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional


# Feature-flag env var for the WNBA strict-parlay engines. Per the
# audit's "feature flag WNBA so it doesn't affect MLB" instruction:
# the parlay-only keys are gated, the per-row engine is always live.
ENV_WNBA_PARLAYS_ENABLED = "EDGE_FEATURE_WNBA_PARLAYS"

# Feature-flag env vars for the NFL + NCAAF strict-parlay engines.
# Same default-off semantics as WNBA so jump-starting football
# doesn't affect live MLB / WNBA cards until the operator flips
# them on for season testing.
ENV_NFL_PARLAYS_ENABLED = "EDGE_FEATURE_NFL_PARLAYS"
ENV_NCAAF_PARLAYS_ENABLED = "EDGE_FEATURE_NCAAF_PARLAYS"


def _wnba_parlays_enabled() -> bool:
    return (
        os.environ.get(ENV_WNBA_PARLAYS_ENABLED, "").strip().lower()
        in {"1", "true", "on", "yes"}
    )


def _nfl_parlays_enabled() -> bool:
    return (
        os.environ.get(ENV_NFL_PARLAYS_ENABLED, "").strip().lower()
        in {"1", "true", "on", "yes"}
    )


def _ncaaf_parlays_enabled() -> bool:
    return (
        os.environ.get(ENV_NCAAF_PARLAYS_ENABLED, "").strip().lower()
        in {"1", "true", "on", "yes"}
    )


# ---------------------------------------------------------------------------
# Lazy factories — each returns the engine instance/class on demand.
# ---------------------------------------------------------------------------


def _factory_nrfi() -> Any:
    # NRFI's run_daily exposes a `main` entry rather than a class — we
    # surface it as a callable runner so registry consumers can invoke
    # it uniformly.
    from edge_equation.engines.nrfi.run_daily import main as nrfi_main
    return nrfi_main


def _factory_mlb_props() -> Any:
    from edge_equation.engines.props_prizepicks.daily import build_props_card
    return build_props_card


def _factory_mlb_fullgame() -> Any:
    from edge_equation.engines.full_game.daily import build_full_game_card
    return build_full_game_card


def _factory_mlb_game_results_parlay() -> Any:
    from edge_equation.engines.mlb.game_results_parlay import (
        MLBGameResultsParlayEngine,
    )
    return MLBGameResultsParlayEngine()


def _factory_mlb_player_props_parlay() -> Any:
    from edge_equation.engines.mlb.player_props_parlay import (
        MLBPlayerPropsParlayEngine,
    )
    return MLBPlayerPropsParlayEngine()


def _factory_mlb_daily() -> Any:
    from edge_equation.engines.mlb.run_daily import MLBDailyRunner
    return MLBDailyRunner()


def _factory_wnba() -> Any:
    from edge_equation.engines.wnba.run_daily import WNBARunner
    return WNBARunner


def _factory_wnba_game_results_parlay() -> Any:
    if not _wnba_parlays_enabled():
        # Feature flag off — registry behaves as if the key didn't
        # exist. The unified WNBA runner can still be invoked
        # directly during testing.
        raise ImportError(
            f"WNBA parlays are feature-flagged off "
            f"(set {ENV_WNBA_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.wnba.game_results_parlay import (
        WNBAGameResultsParlayEngine,
    )
    return WNBAGameResultsParlayEngine()


def _factory_wnba_player_props_parlay() -> Any:
    if not _wnba_parlays_enabled():
        raise ImportError(
            f"WNBA parlays are feature-flagged off "
            f"(set {ENV_WNBA_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.wnba.player_props_parlay import (
        WNBAPlayerPropsParlayEngine,
    )
    return WNBAPlayerPropsParlayEngine()


def _factory_wnba_daily() -> Any:
    if not _wnba_parlays_enabled():
        raise ImportError(
            f"WNBA daily unified runner is feature-flagged off "
            f"(set {ENV_WNBA_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.wnba.parlay_runner import WNBADailyRunner
    return WNBADailyRunner()


def _factory_nfl_game_results_parlay() -> Any:
    if not _nfl_parlays_enabled():
        raise ImportError(
            f"NFL parlays are feature-flagged off "
            f"(set {ENV_NFL_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.nfl.game_results_parlay import (
        NFLGameResultsParlayEngine,
    )
    return NFLGameResultsParlayEngine()


def _factory_nfl_player_props_parlay() -> Any:
    if not _nfl_parlays_enabled():
        raise ImportError(
            f"NFL parlays are feature-flagged off "
            f"(set {ENV_NFL_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.nfl.player_props_parlay import (
        NFLPlayerPropsParlayEngine,
    )
    return NFLPlayerPropsParlayEngine()


def _factory_nfl_daily() -> Any:
    if not _nfl_parlays_enabled():
        raise ImportError(
            f"NFL daily unified runner is feature-flagged off "
            f"(set {ENV_NFL_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.nfl.parlay_runner import NFLDailyRunner
    return NFLDailyRunner()


def _factory_ncaaf_game_results_parlay() -> Any:
    if not _ncaaf_parlays_enabled():
        raise ImportError(
            f"NCAAF parlays are feature-flagged off "
            f"(set {ENV_NCAAF_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.ncaaf.game_results_parlay import (
        NCAAFGameResultsParlayEngine,
    )
    return NCAAFGameResultsParlayEngine()


def _factory_ncaaf_player_props_parlay() -> Any:
    if not _ncaaf_parlays_enabled():
        raise ImportError(
            f"NCAAF parlays are feature-flagged off "
            f"(set {ENV_NCAAF_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.ncaaf.player_props_parlay import (
        NCAAFPlayerPropsParlayEngine,
    )
    return NCAAFPlayerPropsParlayEngine()


def _factory_ncaaf_daily() -> Any:
    if not _ncaaf_parlays_enabled():
        raise ImportError(
            f"NCAAF daily unified runner is feature-flagged off "
            f"(set {ENV_NCAAF_PARLAYS_ENABLED}=on to enable)."
        )
    from edge_equation.engines.ncaaf.parlay_runner import NCAAFDailyRunner
    return NCAAFDailyRunner()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


ENGINE_REGISTRY: dict[str, Callable[[], Any]] = {
    # MLB
    "mlb_nrfi": _factory_nrfi,
    "mlb_props": _factory_mlb_props,
    "mlb_fullgame": _factory_mlb_fullgame,
    "mlb_game_results_parlay": _factory_mlb_game_results_parlay,
    "mlb_player_props_parlay": _factory_mlb_player_props_parlay,
    "mlb_daily": _factory_mlb_daily,
    # WNBA — per-row engine always live; parlay engines + unified
    # runner are gated on EDGE_FEATURE_WNBA_PARLAYS.
    "wnba": _factory_wnba,
    "wnba_game_results_parlay": _factory_wnba_game_results_parlay,
    "wnba_player_props_parlay": _factory_wnba_player_props_parlay,
    "wnba_daily": _factory_wnba_daily,
    # NFL — gated on EDGE_FEATURE_NFL_PARLAYS until 2026 season
    # testing clears.
    "nfl_game_results_parlay": _factory_nfl_game_results_parlay,
    "nfl_player_props_parlay": _factory_nfl_player_props_parlay,
    "nfl_daily": _factory_nfl_daily,
    # NCAAF — gated on EDGE_FEATURE_NCAAF_PARLAYS until 2026 season
    # testing clears.
    "ncaaf_game_results_parlay": _factory_ncaaf_game_results_parlay,
    "ncaaf_player_props_parlay": _factory_ncaaf_player_props_parlay,
    "ncaaf_daily": _factory_ncaaf_daily,
}


def get_engine(engine_key: str) -> Optional[Any]:
    """Return the engine for ``engine_key`` or ``None`` when unregistered.

    Catches ``ImportError`` (and friends) so a missing optional
    dependency — or a feature-flagged-off engine — reads as a missing
    engine rather than crashing the whole runner.
    """
    factory = ENGINE_REGISTRY.get(engine_key)
    if factory is None:
        return None
    try:
        return factory()
    except ImportError:
        return None


def list_engines() -> list[str]:
    """All registered engine keys, sorted for deterministic output."""
    return sorted(ENGINE_REGISTRY.keys())
