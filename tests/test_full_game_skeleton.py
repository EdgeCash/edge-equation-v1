"""Tests for the full-game engine skeleton (FG-1).

Covers config, markets, odds fetcher (mocked httpx), projection
across all six market shapes, edge math + de-vig, and tier
classification routing.
"""

from __future__ import annotations

from typing import Any

import pytest

from edge_equation.engines.full_game import (
    FullGameConfig,
    FullGameLine,
    LEAGUE_RUNS_PER_GAME,
    MLB_FULL_GAME_MARKETS,
    ProjectionKnobs,
    TeamRollingRates,
    bayesian_blend,
    build_devig_table,
    build_edge_picks,
    compute_edge_pp,
    compute_team_rates_from_actuals,
    default_team_rates_table,
    fetch_event_list,
    fetch_event_full_game_props,
    market_for_odds_api_key,
    normalize_event_payload,
    project_all,
    project_full_game_market,
)
from edge_equation.engines.full_game.projection import (
    _poisson_cdf,
    _prob_over_poisson,
    _skellam_p_diff_gt,
)
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------


def test_full_game_markets_cover_core_set():
    canonical = set(MLB_FULL_GAME_MARKETS.keys())
    assert canonical >= {"ML", "Run_Line", "Total", "F5_Total", "F5_ML",
                            "Team_Total"}


def test_market_for_odds_api_key_round_trip():
    for canonical, m in MLB_FULL_GAME_MARKETS.items():
        back = market_for_odds_api_key(m.odds_api_key)
        assert back is not None and back.canonical == canonical


def test_market_for_odds_api_key_unknown_returns_none():
    assert market_for_odds_api_key("not_a_real_market") is None


def test_alternate_markets_flagged():
    """F5_Total / F5_ML / Team_Total need the per-event endpoint
    (paid alternates), so they're flagged for the orchestrator to
    route correctly."""
    assert MLB_FULL_GAME_MARKETS["F5_Total"].requires_alternate
    assert MLB_FULL_GAME_MARKETS["F5_ML"].requires_alternate
    assert MLB_FULL_GAME_MARKETS["Team_Total"].requires_alternate
    assert not MLB_FULL_GAME_MARKETS["ML"].requires_alternate
    assert not MLB_FULL_GAME_MARKETS["Total"].requires_alternate


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_resolves_paths():
    cfg = FullGameConfig().resolve_paths()
    assert cfg.cache_dir.exists()
    assert cfg.duckdb_path.parent.exists()


def test_projection_knobs_have_sensible_defaults():
    k = ProjectionKnobs()
    assert k.lookback_days >= 30
    assert k.prior_weight_games > 0
    assert 0.0 <= k.home_field_advantage_pct <= 0.10
    assert 0.40 <= k.f5_share_of_total <= 0.65


# ---------------------------------------------------------------------------
# Bayesian blend
# ---------------------------------------------------------------------------


def test_blend_zero_n_returns_prior():
    assert bayesian_blend(5.0, 0, 4.55, 12) == 4.55


def test_blend_high_n_approaches_observed():
    out = bayesian_blend(5.0, 1000, 4.55, 12)
    assert abs(out - 5.0) < 0.10


def test_blend_negative_n_treated_as_zero():
    assert bayesian_blend(5.0, -3, 4.55, 12) == 4.55


# ---------------------------------------------------------------------------
# Default team-rates table + actuals aggregation
# ---------------------------------------------------------------------------


def test_default_team_rates_table_covers_supported_teams():
    """30 historical tricodes plus ATH (post-2025 Athletics relocation)."""
    table = default_team_rates_table()
    assert len(table) >= 30
    assert "NYY" in table and "BOS" in table
    # Athletics relocation: keep both OAK (legacy rows) and ATH (current
    # schedule payloads) so neither gets silently dropped.
    assert "OAK" in table
    assert "ATH" in table
    for tri, rates in table.items():
        assert rates.runs_per_game == LEAGUE_RUNS_PER_GAME
        assert rates.n_games == 0


def test_compute_team_rates_from_actuals_basic():
    """3-game sample for NYY: scored 5, 7, 3 — allowed 4, 2, 5."""
    import pandas as pd
    df = pd.DataFrame([
        {"home_team": "NYY", "away_team": "BOS",
           "home_runs": 5, "away_runs": 4},
        {"home_team": "TB",  "away_team": "NYY",
           "home_runs": 2, "away_runs": 7},
        {"home_team": "NYY", "away_team": "TOR",
           "home_runs": 3, "away_runs": 5},
    ])
    rates = compute_team_rates_from_actuals(
        df, team_tricode="NYY", end_date="2026-04-28",
    )
    assert rates.n_games == 3
    assert rates.runs_per_game == pytest.approx(15 / 3)
    assert rates.runs_allowed_per_game == pytest.approx(11 / 3)


def test_compute_team_rates_from_empty_falls_back_to_prior():
    import pandas as pd
    rates = compute_team_rates_from_actuals(
        pd.DataFrame(), team_tricode="NYY", end_date="2026-04-28",
    )
    assert rates.n_games == 0
    assert rates.runs_per_game == LEAGUE_RUNS_PER_GAME


# ---------------------------------------------------------------------------
# load_team_rates_table — DuckDB-backed loader
# ---------------------------------------------------------------------------


class _FakeStore:
    """Mimics FullGameStore.query_df for the loader tests."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def query_df(self, sql: str, params: tuple):
        import pandas as pd
        return pd.DataFrame(self._rows)


def test_load_team_rates_table_returns_complete_dict_with_seeded_priors():
    """Every supported tricode is in the result; teams without
    actuals get the league prior (n_games=0)."""
    from edge_equation.engines.full_game.data.team_rates import (
        load_team_rates_table,
    )
    store = _FakeStore([
        {"game_pk": 1, "event_date": "2026-04-25",
          "home_team": "NYY", "away_team": "BOS",
          "home_runs": 5, "away_runs": 4},
        {"game_pk": 2, "event_date": "2026-04-26",
          "home_team": "NYY", "away_team": "TB",
          "home_runs": 7, "away_runs": 2},
    ])
    table = load_team_rates_table(store, end_date="2026-04-28",
                                      lookback_days=10)
    # NYY had real actuals → real rates.
    assert table["NYY"].n_games == 2
    assert table["NYY"].runs_per_game == pytest.approx(6.0)
    # A team that didn't appear in actuals still gets a row with the prior.
    assert "LAD" in table
    assert table["LAD"].n_games == 0


def test_load_team_rates_table_falls_back_when_query_raises():
    """Missing column / unmigrated DB → fall back to default prior table."""
    from edge_equation.engines.full_game.data.team_rates import (
        load_team_rates_table,
    )

    class _Broken:
        def query_df(self, sql, params):
            raise RuntimeError("table fullgame_actuals does not exist")

    table = load_team_rates_table(_Broken(), end_date="2026-04-28",
                                      lookback_days=10)
    # Every tricode is league-prior.
    assert all(r.n_games == 0 for r in table.values())
    assert "NYY" in table


def test_load_team_rates_table_uses_lookback_window_in_query():
    """end - lookback_days should bound the team-rates query."""
    from edge_equation.engines.full_game.data.team_rates import (
        load_team_rates_table,
    )
    captured: list = []

    class _Spy:
        def query_df(self, sql, params):
            # Capture every call so the test can assert on the
            # team-rates query specifically (the loader also runs a
            # secondary diagnostic query with a different parameter
            # shape).
            captured.append((sql, params))
            import pandas as pd
            return pd.DataFrame()

    load_team_rates_table(_Spy(), end_date="2026-05-01", lookback_days=14)
    # First query is the main team-rates SELECT. Find it by SQL shape.
    main_query = next(
        (params for sql, params in captured
          if "SELECT\n    game_pk" in sql or "game_pk, event_date" in sql),
        None,
    )
    assert main_query is not None, f"team-rates query not found in {captured!r}"
    start, end = main_query
    assert end == "2026-05-01"
    assert start == "2026-04-17"   # 14 days back


# ---------------------------------------------------------------------------
# Poisson + Skellam math
# ---------------------------------------------------------------------------


def test_poisson_cdf_zero_lam_is_one():
    assert _poisson_cdf(0, 0.0) == 1.0
    assert _poisson_cdf(8, 0.0) == 1.0


def test_prob_over_full_game_total_matches_intuition():
    """λ=8.5 puts ~50% mass at exactly 8 — Over 8.5 should be near 50%."""
    p = _prob_over_poisson(8.5, 8.5)
    assert 0.45 < p < 0.55


def test_skellam_p_home_wins_when_offenses_equal():
    """λ_h=λ_a → P(margin > 0) ≈ 0.5 (slight nudge from discrete mass at 0)."""
    p = _skellam_p_diff_gt(0.0, 4.5, 4.5)
    assert 0.40 < p < 0.50  # less than 0.5 because a tie is not a "win"


def test_skellam_high_lam_home_dominates():
    """λ_h=6, λ_a=3 → home wins much more often."""
    p = _skellam_p_diff_gt(0.0, 6.0, 3.0)
    assert p > 0.7


def test_skellam_run_line_threshold():
    """Threshold of 1.5 (must win by 2+) is harder than threshold 0
    (must win by 1+)."""
    p_ml = _skellam_p_diff_gt(0.0, 5.0, 4.5)
    p_rl = _skellam_p_diff_gt(1.5, 5.0, 4.5)
    assert p_ml > p_rl


# ---------------------------------------------------------------------------
# Per-market projection
# ---------------------------------------------------------------------------


def _line(canonical="Total", side="Over", line_value=8.5,
            american_odds=-110, home_tricode="NYY", away_tricode="BOS",
            team_tricode=""):
    m = MLB_FULL_GAME_MARKETS[canonical]
    return FullGameLine(
        event_id="evt1", home_team="New York Yankees",
        away_team="Boston Red Sox",
        home_tricode=home_tricode, away_tricode=away_tricode,
        commence_time="2026-04-29T23:05:00Z", market=m,
        side=side, line_value=line_value,
        american_odds=float(american_odds),
        decimal_odds=1.91 if american_odds < 0 else 2.5,
        book="draftkings", team_tricode=team_tricode,
    )


def _team(tri, rpg=4.55, ra=4.55, n=40):
    return TeamRollingRates(
        team_tricode=tri, n_games=n,
        end_date="2026-04-28", lookback_days=45,
        runs_per_game=rpg, runs_allowed_per_game=ra,
    )


def test_total_over_projection_with_high_offenses():
    """Both teams 5.5 RPG → λ_total ~12 → Over 8.5 well above 50%."""
    proj = project_full_game_market(
        _line(canonical="Total", side="Over", line_value=8.5),
        home_rates=_team("NYY", rpg=5.5, ra=4.5),
        away_rates=_team("BOS", rpg=5.5, ra=4.5),
    )
    assert proj.model_prob > 0.70
    assert proj.lam_used > 9.5


def test_total_under_complements_over():
    over = project_full_game_market(
        _line(canonical="Total", side="Over", line_value=8.5),
        home_rates=_team("NYY"), away_rates=_team("BOS"),
    )
    under = project_full_game_market(
        _line(canonical="Total", side="Under", line_value=8.5),
        home_rates=_team("NYY"), away_rates=_team("BOS"),
    )
    assert over.model_prob + under.model_prob == pytest.approx(1.0, abs=1e-9)


def test_f5_total_smaller_than_full_game_total():
    """F5_Total uses ~55% of full-game λ → P(over 4.5) < P(over 8.5)
    on a similarly-paced game (both lines centered around λ)."""
    full = project_full_game_market(
        _line(canonical="Total", side="Over", line_value=8.5),
        home_rates=_team("NYY"), away_rates=_team("BOS"),
    )
    f5 = project_full_game_market(
        _line(canonical="F5_Total", side="Over", line_value=4.5),
        home_rates=_team("NYY"), away_rates=_team("BOS"),
    )
    assert f5.lam_used < full.lam_used


def test_team_total_uses_team_specific_lambda():
    """Home pick + home tricode → use λ_home, not λ_total."""
    proj = project_full_game_market(
        _line(canonical="Team_Total", side="Over", line_value=4.5,
                team_tricode="NYY"),
        home_rates=_team("NYY", rpg=6.0), away_rates=_team("BOS", rpg=4.0),
    )
    # λ_home for the NYY/BOS matchup with NYY 6.0 / BOS 4.0 is around 5+.
    assert proj.lam_used > 4.5
    assert proj.lam_used < proj.lam_home + proj.lam_away


def test_moneyline_home_pick_when_home_dominates():
    """NYY massively outclasses BOS → P(NYY wins) high."""
    proj = project_full_game_market(
        _line(canonical="ML", side="NYY", line_value=None,
                team_tricode="NYY", american_odds=-200),
        home_rates=_team("NYY", rpg=6.0, ra=3.5),
        away_rates=_team("BOS", rpg=3.8, ra=5.2),
    )
    assert proj.model_prob > 0.65


def test_moneyline_away_pick_complement():
    """Away ML on BOS — when home dominates, BOS prob low."""
    proj = project_full_game_market(
        _line(canonical="ML", side="BOS", line_value=None,
                team_tricode="BOS", american_odds=+170),
        home_rates=_team("NYY", rpg=6.0, ra=3.5),
        away_rates=_team("BOS", rpg=3.8, ra=5.2),
    )
    assert proj.model_prob < 0.40


def test_run_line_minus_15_harder_than_moneyline():
    """A favourite at -1.5 must win by 2+ — strictly harder than ML."""
    ml = project_full_game_market(
        _line(canonical="ML", side="NYY", line_value=None,
                team_tricode="NYY", american_odds=-200),
        home_rates=_team("NYY", rpg=6.0, ra=3.5),
        away_rates=_team("BOS", rpg=3.8, ra=5.2),
    )
    rl = project_full_game_market(
        _line(canonical="Run_Line", side="NYY", line_value=-1.5,
                team_tricode="NYY", american_odds=+105),
        home_rates=_team("NYY", rpg=6.0, ra=3.5),
        away_rates=_team("BOS", rpg=3.8, ra=5.2),
    )
    assert rl.model_prob < ml.model_prob


def test_run_line_plus_15_easier_than_underdog_moneyline():
    """A dog at +1.5 only needs to lose by ≤1 → easier than the ML."""
    ml = project_full_game_market(
        _line(canonical="ML", side="BOS", line_value=None,
                team_tricode="BOS", american_odds=+170),
        home_rates=_team("NYY", rpg=6.0, ra=3.5),
        away_rates=_team("BOS", rpg=3.8, ra=5.2),
    )
    rl = project_full_game_market(
        _line(canonical="Run_Line", side="BOS", line_value=+1.5,
                team_tricode="BOS", american_odds=-130),
        home_rates=_team("NYY", rpg=6.0, ra=3.5),
        away_rates=_team("BOS", rpg=3.8, ra=5.2),
    )
    assert rl.model_prob > ml.model_prob


def test_projection_no_rates_uses_league_prior():
    """Both rates None → λ_home = λ_away ≈ league average × HFA."""
    proj = project_full_game_market(
        _line(canonical="Total", side="Over", line_value=8.5),
    )
    # λ_total = 2 × LEAGUE_RPG × (1 + half-HFA) ≈ 9.2-9.5.
    assert 8.5 < proj.lam_used < 10.0
    assert proj.confidence == pytest.approx(0.30)


def test_project_all_preserves_input_order():
    lines = [
        _line(canonical="Total", side="Over", line_value=8.5),
        _line(canonical="Total", side="Under", line_value=8.5),
        _line(canonical="ML", side="NYY", line_value=None,
                team_tricode="NYY", american_odds=-150),
    ]
    out = project_all(lines)
    assert len(out) == 3
    assert out[0].market.canonical == "Total" and out[0].side == "Over"
    assert out[2].market.canonical == "ML"


# ---------------------------------------------------------------------------
# De-vig + edge math
# ---------------------------------------------------------------------------


def test_devig_table_pairs_over_under():
    over = _line(side="Over", line_value=8.5, american_odds=-110)
    under = _line(side="Under", line_value=8.5, american_odds=-110)
    table = build_devig_table([over, under])
    assert len(table) == 1
    assert 1.04 < list(table.values())[0] < 1.10


def test_devig_table_pairs_moneyline_sides():
    """ML on home + away with line_value=None must still pair."""
    home = _line(canonical="ML", side="NYY", line_value=None,
                   team_tricode="NYY", american_odds=-150)
    away = _line(canonical="ML", side="BOS", line_value=None,
                   team_tricode="BOS", american_odds=+130)
    table = build_devig_table([home, away])
    assert len(table) == 1


def test_compute_edge_pp_positive_when_model_beats_book():
    line = _line(canonical="Total", side="Over", line_value=8.5,
                   american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.62, confidence=0.60,
    )
    edge_pp, raw, devigged, corrected = compute_edge_pp(
        line=line, projection=proj,
    )
    assert edge_pp > 0
    assert corrected is False


def test_compute_edge_pp_devig_shrinks_market_prob():
    line = _line(canonical="Total", side="Over", line_value=8.5,
                   american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.55, confidence=0.60,
    )
    edge_pp_no_devig, _, _, _ = compute_edge_pp(line=line, projection=proj)
    edge_pp_devig, _, devigged, corrected = compute_edge_pp(
        line=line, projection=proj, devig_total=1.05,
    )
    assert corrected is True
    assert edge_pp_devig > edge_pp_no_devig  # de-vigging widens the edge


# ---------------------------------------------------------------------------
# build_edge_picks
# ---------------------------------------------------------------------------


def test_build_edge_picks_filters_below_min_tier():
    """A 0.5pp edge classifies as NO_PLAY and must not be returned."""
    line = _line(side="Over", line_value=8.5, american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    # Confidence above the default min_confidence floor so this test
    # exercises the tier filter, not the pure-prior floor.
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.527, confidence=0.50,
    )
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert picks == []


def test_build_edge_picks_skips_pure_prior_projections_by_default():
    """confidence==0.30 means projection rests entirely on the league
    prior — shouldn't reach the public-facing pick list."""
    line = _line(side="Over", line_value=8.5, american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    # Wide model_prob → would otherwise classify as ELITE / STRONG.
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.70, confidence=0.30,
    )
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert picks == []
    # Explicit override keeps backtest-style callers working.
    picks_no_floor = build_edge_picks(
        [line], [proj], min_tier=Tier.LEAN, min_confidence=0.0,
    )
    assert len(picks_no_floor) == 1


def test_build_edge_picks_keeps_high_confidence_picks():
    """confidence above the floor lets the pick through to tier classification."""
    line = _line(side="Over", line_value=8.5, american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.58, confidence=0.55,
    )
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert len(picks) == 1


def test_build_edge_picks_classifies_moderate_at_5pp():
    """Post 2026-05-02 ladder: 12/8/5/2.5pp edge. ~5.5pp lands in MODERATE."""
    line = _line(side="Over", line_value=8.5, american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.58, confidence=0.50,
    )
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert len(picks) == 1
    assert picks[0].tier == Tier.MODERATE
    assert picks[0].edge_pp > 5.0


def test_build_edge_picks_classifies_strong_at_8pp_with_high_prob():
    """8pp edge with model_prob above the 0.62 ELITE floor → STRONG."""
    line = _line(side="Over", line_value=8.5, american_odds=-110)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.62, confidence=0.55,
    )
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert len(picks) == 1
    assert picks[0].tier == Tier.STRONG


def test_build_edge_picks_demotes_elite_when_model_prob_below_floor():
    """A 14pp edge with model_prob 0.46 → demote ELITE → STRONG."""
    line = _line(side="Over", line_value=8.5, american_odds=+200)
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    proj = ProjectedFullGameSide(
        market=line.market, side="Over", line_value=8.5,
        model_prob=0.47, confidence=0.55,
    )
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert len(picks) == 1
    assert picks[0].tier == Tier.STRONG
    assert picks[0].edge_pp > 12.0


def test_build_edge_picks_sorts_by_edge_desc():
    a = _line(canonical="Total", side="Over", line_value=8.5,
                american_odds=+200)  # implied 33%
    b = _line(canonical="Total", side="Over", line_value=8.5,
                american_odds=-110)  # implied 52.4%
    from edge_equation.engines.full_game.projection import (
        ProjectedFullGameSide,
    )
    a_proj = ProjectedFullGameSide(market=a.market, side="Over",
                                       line_value=8.5,
                                       model_prob=0.45, confidence=0.5)
    b_proj = ProjectedFullGameSide(market=b.market, side="Over",
                                       line_value=8.5,
                                       model_prob=0.58, confidence=0.5)
    picks = build_edge_picks([a, b], [a_proj, b_proj],
                                min_tier=Tier.LEAN)
    edges = [p.edge_pp for p in picks]
    assert edges == sorted(edges, reverse=True)


def test_build_edge_picks_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        build_edge_picks([_line()], [], min_tier=Tier.LEAN)


# ---------------------------------------------------------------------------
# Odds API normalization (mocked httpx)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status
    def json(self): return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)
    def close(self): pass


def test_normalize_event_payload_extracts_total_h2h_and_spreads():
    payload = {
        "id": "evt1", "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "commence_time": "2026-04-29T23:05:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "totals", "outcomes": [
                    {"name": "Over",  "point": 8.5, "price": -110},
                    {"name": "Under", "point": 8.5, "price": -110},
                ]},
                {"key": "h2h", "outcomes": [
                    {"name": "New York Yankees", "price": -150},
                    {"name": "Boston Red Sox",   "price": +130},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": "New York Yankees", "point": -1.5, "price": +130},
                    {"name": "Boston Red Sox",   "point": +1.5, "price": -150},
                ]},
            ]},
        ],
    }
    rows = normalize_event_payload(payload)
    by_key = {(r.market.canonical, r.side, r.line_value): r for r in rows}
    assert ("Total", "Over",  8.5) in by_key
    assert ("Total", "Under", 8.5) in by_key
    # ML uses tricode side
    assert ("ML", "NYY", None) in by_key
    assert ("ML", "BOS", None) in by_key
    # Run_Line carries spread + tricode
    rl_nyy = by_key[("Run_Line", "NYY", -1.5)]
    assert rl_nyy.team_tricode == "NYY"


def test_normalize_event_payload_filters_by_canonical_set():
    payload = {
        "id": "evt1", "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "commence_time": "2026-04-29T23:05:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "totals", "outcomes": [
                    {"name": "Over",  "point": 8.5, "price": -110},
                ]},
                {"key": "h2h", "outcomes": [
                    {"name": "New York Yankees", "price": -150},
                ]},
            ]},
        ],
    }
    rows = normalize_event_payload(payload, canonical_filter={"Total"})
    assert all(r.market.canonical == "Total" for r in rows)


def test_normalize_event_payload_unknown_team_keeps_empty_tricode():
    """Foreign-league names that don't map to MLB tricodes don't crash;
    the row passes through with team_tricode='' for ML rows."""
    payload = {
        "id": "evt1", "home_team": "Unknown FC",
        "away_team": "Madrid CF",
        "commence_time": "2026-04-29T23:05:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Unknown FC", "price": -150},
                    {"name": "Madrid CF",  "price": +130},
                ]},
            ]},
        ],
    }
    rows = normalize_event_payload(payload)
    # Both sides emitted; tricode falls back to the full-name string.
    assert all(r.team_tricode == "" for r in rows)


def test_fetch_event_list_threads_apikey():
    client = _FakeClient([_FakeResponse([])])
    fetch_event_list(http_client=client, api_key="secret")
    _, params = client.calls[0]
    assert params["apiKey"] == "secret"
    assert "h2h" in params["markets"]
    assert "spreads" in params["markets"]
    assert "totals" in params["markets"]


def test_fetch_event_full_game_props_uses_alt_only_default():
    client = _FakeClient([_FakeResponse({"bookmakers": []})])
    fetch_event_full_game_props(
        event_id="evt1", api_key="secret", http_client=client,
    )
    _, params = client.calls[0]
    # Default is alt-only — must NOT include `h2h` or `totals` (those
    # are already in the event-list call).
    assert "h2h," not in params["markets"]  # rough check
    assert "totals_1st_5_innings" in params["markets"]
