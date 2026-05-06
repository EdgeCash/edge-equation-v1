"""Global engine registry for Edge Equation.

Maps an engine key → (lazy) factory that returns the engine's runner.
Lazy because not every engine is importable in every environment
(NRFI's [nrfi] extras include xgboost / shap / pybaseball; the
unified MLB runner imports the optional Odds API client). Calling
``get_engine(...)`` is the only way an engine gets imported, so a
single missing extra doesn't take the whole registry down.

Register every engine the system can run here. Each value is a
zero-arg factory that returns an engine instance. The MLB scope
covers:

* ``mlb_nrfi``                — NRFI / YRFI engine (existing).
* ``mlb_props``               — Player props (PrizePicks / standard
                                 books, existing).
* ``mlb_fullgame``            — Full-game ML / RL / Total / Team
                                 Total / F5 (existing).
* ``mlb_game_results_parlay`` — Strict 3–6 leg game-results parlay
                                 (NEW).
* ``mlb_player_props_parlay`` — Strict 3–6 leg player-props parlay
                                 (NEW).
* ``mlb_daily``               — Unified MLB daily runner that produces
                                 the full card (all markets + both
                                 parlay types) in a single pass (NEW).

Non-MLB sports stay registered for parity with the rest of the
codebase but are out of scope for the current MLB-finalize work.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


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
    # Non-MLB (out of scope for the current work)
    "wnba": _factory_wnba,
}


def get_engine(engine_key: str) -> Optional[Any]:
    """Return the engine for ``engine_key`` or ``None`` when unregistered.

    Catches ``ImportError`` (and friends) so a missing optional
    dependency reads as a missing engine rather than crashing the
    whole runner.
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
