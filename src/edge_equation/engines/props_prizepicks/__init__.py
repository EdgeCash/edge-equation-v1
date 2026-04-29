"""MLB player-props engine — Phase 4 skeleton.

What's wired
------------

* ``markets.py`` — canonical market constants + Odds API key mapping.
* ``odds_fetcher.py`` — hits The Odds API per-event endpoint to pull
  player-prop lines (HR / Hits / Total Bases / RBI / K).
* ``projection.py`` — naive Poisson baseline projection. League-average
  λ per market today; per-player Statcast λ in a follow-up PR.
* ``edge.py`` — vig-adjusted edge computation + tier classification
  (uses the engine-wide `tiering.classify_tier` edge ladder).

What's NOT wired yet
--------------------

* Statcast features (per-player rate) → projection.
* Daily-email surface — picks aren't shown in the email yet; this
  package emits `PropEdgePick` rows that a later PR will route through
  `posting/player_props.py` for rendering.
* Settlement / ledger persistence — props ledger lives separately
  from NRFI's so this is a future concern.

Usage example
-------------

::

    from edge_equation.engines.props_prizepicks import (
        fetch_all_player_props, project_all, build_edge_picks,
    )
    lines = fetch_all_player_props(target_date="2026-04-29")
    projections = project_all(lines)
    picks = build_edge_picks(lines, projections)
    # picks: list[PropEdgePick] sorted by edge desc
"""

from __future__ import annotations

from .config import (
    APIConfig,
    ProjectionKnobs,
    PropsConfig,
    get_default_config,
)
from .data.statcast_loader import (
    BatterRollingRates,
    LEAGUE_BATTER_PRIOR_PER_PA,
    LEAGUE_PITCHER_PRIOR_PER_BF,
    PitcherRollingRates,
    bayesian_blend,
    compute_batter_rates_from_statcast,
    compute_pitcher_rates_from_statcast,
    load_batter_rates,
    load_pitcher_rates,
)
from .data.storage import PropsStore
from .edge import (
    PropEdgePick,
    build_devig_table,
    build_edge_picks,
    compute_edge_pp,
)
from .markets import (
    MLB_PROP_MARKETS,
    ODDS_API_MARKETS_PARAM,
    PropMarket,
    all_markets,
    market_for_odds_api_key,
)
from .odds_fetcher import (
    PlayerPropLine,
    fetch_all_player_props,
    fetch_event_list,
    fetch_event_player_props,
    normalize_event_payload,
)
from .ledger import (
    SettlementResult,
    get_tier_ledger,
    init_ledger_tables,
    render_ledger_section,
    settle_predictions,
)
from .output import (
    PropOutput,
    build_prop_output,
    color_band_for_tier,
    color_hex_for_tier,
    to_api_dict,
    to_email_card,
)
from .projection import (
    ProjectedSide,
    project_all,
    project_player_market_prob,
)


__all__ = [
    # config
    "APIConfig",
    "ProjectionKnobs",
    "PropsConfig",
    "get_default_config",
    # data
    "BatterRollingRates",
    "LEAGUE_BATTER_PRIOR_PER_PA",
    "LEAGUE_PITCHER_PRIOR_PER_BF",
    "PitcherRollingRates",
    "PropsStore",
    "bayesian_blend",
    "compute_batter_rates_from_statcast",
    "compute_pitcher_rates_from_statcast",
    "load_batter_rates",
    "load_pitcher_rates",
    # markets
    "MLB_PROP_MARKETS",
    "ODDS_API_MARKETS_PARAM",
    "PropMarket",
    "all_markets",
    "market_for_odds_api_key",
    # odds_fetcher
    "PlayerPropLine",
    "fetch_all_player_props",
    "fetch_event_list",
    "fetch_event_player_props",
    "normalize_event_payload",
    # projection
    "ProjectedSide",
    "project_all",
    "project_player_market_prob",
    # edge
    "PropEdgePick",
    "build_devig_table",
    "build_edge_picks",
    "compute_edge_pp",
    # output
    "PropOutput",
    "build_prop_output",
    "color_band_for_tier",
    "color_hex_for_tier",
    "to_api_dict",
    "to_email_card",
    # ledger
    "SettlementResult",
    "get_tier_ledger",
    "init_ledger_tables",
    "render_ledger_section",
    "settle_predictions",
]
