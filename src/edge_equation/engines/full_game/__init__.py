"""MLB full-game engine — Phase 5.

Mirrors the elite NRFI / Props pattern:

* `config.py`         — `FullGameConfig` + tunable knobs.
* `markets.py`        — canonical → Odds API mapping for ML, Run_Line,
                          Total, F5_Total, F5_ML, Team_Total.
* `odds_fetcher.py`   — standard + alternate market fetchers + payload
                          normalization to typed `FullGameLine` rows.
* `projection.py`     — per-team Bayesian-blended Poisson + Skellam
                          for spread / moneyline. One projector handles
                          all six markets via dispatch on canonical name.
* `edge.py`           — vig-adjusted edge computation + tier
                          classification (uses the engine-wide
                          `tiering.classify_tier` edge ladder).
* `data/storage.py`   — `FullGameStore` + DDL.
* `data/team_rates.py` — `TeamRollingRates`, `bayesian_blend`,
                            `compute_team_rates_from_actuals`.

What's NOT wired yet (FG-2 / FG-3):

* `output/payload.py`     — `FullGameOutput` mirroring NRFIOutput/PropOutput.
* `ledger.py`             — per-tier YTD ledger.
* `daily.py`              — daily orchestrator.
* `run_daily.py` integration.
"""

from __future__ import annotations

from .config import (
    APIConfig,
    FullGameConfig,
    ProjectionKnobs,
    get_default_config,
)
from .data.storage import FullGameStore
from .data.team_rates import (
    LEAGUE_RUNS_ALLOWED_PER_GAME,
    LEAGUE_RUNS_PER_GAME,
    TeamRollingRates,
    bayesian_blend,
    compute_team_rates_from_actuals,
    default_team_rates_table,
)
from .edge import (
    FullGameEdgePick,
    build_devig_table,
    build_edge_picks,
    compute_edge_pp,
)
from .markets import (
    ALL_MARKETS_PARAM,
    MLB_FULL_GAME_MARKETS,
    STANDARD_MARKETS_PARAM,
    FullGameMarket,
    all_markets,
    market_for_odds_api_key,
)
from .odds_fetcher import (
    FullGameLine,
    fetch_all_full_game_lines,
    fetch_event_full_game_props,
    fetch_event_list,
    normalize_event_payload,
)
from .projection import (
    ProjectedFullGameSide,
    project_all,
    project_full_game_market,
)


__all__ = [
    # config
    "APIConfig",
    "FullGameConfig",
    "ProjectionKnobs",
    "get_default_config",
    # data
    "FullGameStore",
    "LEAGUE_RUNS_ALLOWED_PER_GAME",
    "LEAGUE_RUNS_PER_GAME",
    "TeamRollingRates",
    "bayesian_blend",
    "compute_team_rates_from_actuals",
    "default_team_rates_table",
    # markets
    "ALL_MARKETS_PARAM",
    "MLB_FULL_GAME_MARKETS",
    "STANDARD_MARKETS_PARAM",
    "FullGameMarket",
    "all_markets",
    "market_for_odds_api_key",
    # odds_fetcher
    "FullGameLine",
    "fetch_all_full_game_lines",
    "fetch_event_full_game_props",
    "fetch_event_list",
    "normalize_event_payload",
    # projection
    "ProjectedFullGameSide",
    "project_all",
    "project_full_game_market",
    # edge
    "FullGameEdgePick",
    "build_devig_table",
    "build_edge_picks",
    "compute_edge_pp",
]
