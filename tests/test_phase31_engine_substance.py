"""
Phase 31 -- engine substance: real strengths, specific Reads, slate lockdown.

Locks in five behavior changes from the dry-run review:

  1. TeamStrengthBuilder.build() returns a deterministic per-team seed
     near 1.0 (not the literal NEUTRAL_STRENGTH) when no settled games
     exist. Prevents Bradley-Terry collapsing to 50/50 on both sides,
     which trips the Phase 28 sanity ceiling post-side-flip and zeros
     the slate.

  2. FeatureComposer.enrich_markets stashes meta["read_context"] --
     recent form, run diff, Elo diff, games_used, sample_warning. The
     betting engine consumes those keys to produce a substantive Read.

  3. _baseline_read NEVER emits the old generic placeholders:
        - "Strengths within noise ... edge lives in the price"
        - "Edge derived from price/probability delta vs market consensus"
     Either it has real evidence to quote or the slot stays minimal
     (MC band only).

  4. PROP_MARKETS admits NRFI/YRFI/First_Inning_Run AND
     PostingFormatter.filter_daily_edge drops KBO/NPB/Soccer picks so
     overseas rows can't leak into a domestic card.

  5. MVS rendering always produces a substantive Read -- when the
     engine didn't stash anything bespoke the renderer cites the four
     MVS thresholds themselves (grade, edge, Kelly, MC band) so the
     highest-confidence signal is never a blank placeholder.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.betting_engine import BettingEngine, _baseline_read
from edge_equation.engine.feature_builder import FeatureBuilder, FeatureBundle
from edge_equation.engine.major_variance import META_KEY as MVS_META_KEY
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.player_props import (
    PROP_MARKETS,
    PROP_MARKET_LABEL,
    render_prop_section,
)
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import _render_mvs_block
from edge_equation.stats.composer import FeatureComposer
from edge_equation.stats.results import GameResult
from edge_equation.stats.team_strength import (
    NEUTRAL_STRENGTH,
    TeamStrengthBuilder,
)


# ---------------------------------------------- cold-start strength seed

def test_cold_start_strengths_are_not_exactly_one():
    """Two teams with no settled history used to both get 1.0 -> BT
    collapsed to 50/50 -> the sanity guard fired on the away side
    post-flip -> the slate shipped empty."""
    a = TeamStrengthBuilder.build(team="A", league="MLB", results=[])
    b = TeamStrengthBuilder.build(team="B", league="MLB", results=[])
    assert a.strength != Decimal("1.000000")
    assert b.strength != Decimal("1.000000")
    assert a.strength != b.strength


def test_cold_start_strengths_are_deterministic():
    a1 = TeamStrengthBuilder.build(team="A", league="MLB", results=[])
    a2 = TeamStrengthBuilder.build(team="A", league="MLB", results=[])
    assert a1.strength == a2.strength


def test_cold_start_strengths_stay_within_seed_cap():
    """Seed perturbation is bounded so it never looks like a real edge
    signal on its own. +/- 3% around neutral is the design bar."""
    for team in ("NYY", "BOS", "LAA", "TEX", "SEA", "OAK"):
        ts = TeamStrengthBuilder.build(team=team, league="MLB", results=[])
        assert abs(ts.strength - NEUTRAL_STRENGTH) <= Decimal("0.030001")


def test_real_history_overrides_seed():
    """When there IS settled data the seed path is not taken at all."""
    games = [
        GameResult(
            result_id=None,
            game_id=f"G{i}", league="MLB",
            home_team="A", away_team="B",
            start_time=f"2026-04-{i+1:02d}T18:30:00",
            home_score=8, away_score=2, status="final",
        )
        for i in range(20)
    ]
    from edge_equation.stats.elo import EloCalculator
    elo = EloCalculator.replay("MLB", games)
    ts = TeamStrengthBuilder.build(
        team="A", league="MLB", results=games, elo=elo,
    )
    assert ts.games_used > 0
    # 20-game sweep definitively above the seed band.
    assert ts.strength > Decimal("1.2")


# ---------------------------------------------- read_context composer stash

def _sample_results(n=12):
    return [
        GameResult(
            result_id=None,
            game_id=f"G{i}", league="MLB",
            home_team="NYY" if i % 2 == 0 else "BOS",
            away_team="BOS" if i % 2 == 0 else "NYY",
            start_time=f"2026-04-{i+1:02d}T18:00:00",
            home_score=6 if i % 2 == 0 else 3,
            away_score=3 if i % 2 == 0 else 6,
            status="final",
        )
        for i in range(n)
    ]


def test_enrich_markets_stashes_read_context():
    games = [{"game_id": "X", "league": "MLB",
              "home_team": "NYY", "away_team": "BOS"}]
    markets = [{"game_id": "X", "market_type": "ML",
                "selection": "NYY", "meta": {}}]
    out = FeatureComposer.enrich_markets(markets, games, "MLB", _sample_results())
    rc = out[0]["meta"]["read_context"]
    assert "recent_form_home" in rc
    assert "recent_form_away" in rc
    assert "run_diff_home" in rc
    assert "games_used_home" in rc


def test_enrich_markets_runs_with_empty_results():
    """Phase 31: even with zero settled history the composer still
    populates inputs (via seed) so slate_runner doesn't skip the
    market entirely."""
    games = [{"game_id": "X", "league": "MLB",
              "home_team": "NYY", "away_team": "BOS"}]
    markets = [{"game_id": "X", "market_type": "ML",
                "selection": "NYY", "meta": {}}]
    out = FeatureComposer.enrich_markets(markets, games, "MLB", [])
    assert out[0]["meta"].get("inputs") is not None
    assert "strength_home" in out[0]["meta"]["inputs"]


def test_enrich_markets_sample_warning_when_history_thin():
    games = [{"game_id": "X", "league": "MLB",
              "home_team": "NYY", "away_team": "BOS"}]
    markets = [{"game_id": "X", "market_type": "ML",
                "selection": "NYY", "meta": {}}]
    out = FeatureComposer.enrich_markets(markets, games, "MLB", _sample_results(n=3))
    assert out[0]["meta"]["read_context"].get("sample_warning") is True


# ---------------------------------------------- _baseline_read specificity

def _bundle_with(meta):
    return FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.25, "strength_away": 1.10, "home_adv": 0.115},
        universal_features={},
        selection="NYY",
        metadata={"home_team": "NYY", "away_team": "BOS", **meta},
    )


def test_baseline_read_never_emits_generic_price_probability_prose():
    out = _baseline_read(
        market_type="ML", selection="NYY",
        bundle=_bundle_with({}),
        fair_prob=Decimal("0.55"), edge=Decimal("0.06"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "edge derived from price/probability" not in out.lower()


def test_baseline_read_never_emits_strengths_within_noise_prose():
    out = _baseline_read(
        market_type="ML", selection="NYY",
        bundle=_bundle_with({"read_context": {
            "games_used_home": 12, "games_used_away": 12,
        }}),
        fair_prob=Decimal("0.52"), edge=Decimal("0.04"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "strengths within noise" not in out.lower()
    assert "edge lives in the price" not in out.lower()


def test_baseline_read_surfaces_recent_form_when_present():
    out = _baseline_read(
        market_type="ML", selection="NYY",
        bundle=_bundle_with({"read_context": {
            "games_used_home": 11, "games_used_away": 11,
            "recent_form_home": "8-3 L11", "recent_form_away": "5-6 L11",
            "run_diff_home": 18, "run_diff_away": -5,
        }}),
        fair_prob=Decimal("0.58"), edge=Decimal("0.05"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "8-3 L11" in out
    assert "5-6 L11" in out
    assert "+18" in out


def test_baseline_read_surfaces_weather_and_umpire():
    bundle = _bundle_with({
        "weather": {"wind_mph": 18, "wind_dir": "out to right", "temp_f": 54},
        "umpire": {"name": "Angel Hernandez", "k_factor": 1.07},
    })
    out = _baseline_read(
        market_type="ML", selection="NYY", bundle=bundle,
        fair_prob=Decimal("0.55"), edge=Decimal("0.05"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "wind 18 mph out to right" in out
    assert "54°F" in out
    assert "Angel Hernandez" in out


def test_baseline_read_surfaces_pitching_matchup():
    bundle = _bundle_with({
        "pitching_home": {"name": "Cole", "fip": 2.95},
        "pitching_away": {"name": "Houck", "fip": 4.10},
        "read_context": {
            "games_used_home": 10, "games_used_away": 10,
        },
    })
    out = _baseline_read(
        market_type="ML", selection="NYY", bundle=bundle,
        fair_prob=Decimal("0.60"), edge=Decimal("0.07"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "Cole 2.95" in out
    assert "Houck 4.10" in out


def test_baseline_read_sample_warning_surfaces():
    out = _baseline_read(
        market_type="ML", selection="NYY",
        bundle=_bundle_with({"read_context": {
            "games_used_home": 2, "games_used_away": 2,
            "sample_warning": True,
        }}),
        fair_prob=Decimal("0.52"), edge=Decimal("0.03"),
        hfa_value=None, decay_halflife_days=None,
    )
    assert "Limited settled-game history" in out


# ---------------------------------------------- prop section hardening

def test_prop_markets_include_first_inning_and_nrfi():
    assert "NRFI" in PROP_MARKETS
    assert "YRFI" in PROP_MARKETS
    assert "First_Inning_Run" in PROP_MARKETS
    assert PROP_MARKET_LABEL["NRFI"] == "No Runs 1st Inning"


def _prop_pick(market, grade="A+", edge="0.10", player=None, game_id="G1"):
    meta = {"home_team": "NYY", "away_team": "BOS"}
    if player:
        meta["player_name"] = player
    return Pick(
        sport="MLB", market_type=market,
        selection=(f"{player} over 0.5" if player else "Yes"),
        line=Line(odds=-110),
        fair_prob=Decimal("0.55"),
        expected_value=Decimal("0.82"),
        edge=Decimal(edge),
        kelly=Decimal("0.04"),
        grade=grade,
        game_id=game_id,
        metadata=meta,
    )


def test_prop_renderer_aligned_text_table():
    picks = [
        _prop_pick("HR", player="Aaron Judge"),
        _prop_pick("K", player="Gerrit Cole", game_id="G2"),
    ]
    text = render_prop_section(picks, date_str="2026-04-22")
    # Header uses short column names so it fits 80-col email clients.
    assert "Proj" in text
    assert "Gr" in text
    # Rows still pipe-delimited for the legacy consumer that parses the
    # table as CSV-like.
    assert "|" in text
    # Divider line is composed of dashes + "+-".
    assert "---" in text


def test_prop_renderer_surfaces_nrfi_as_game_level_row():
    p = _prop_pick("NRFI", grade="A+", game_id="NYY-BOS")
    text = render_prop_section([p], date_str="2026-04-22")
    # Game-level props render "<away> @ <home>" in the player column.
    assert "BOS @ NYY" in text
    assert "No Runs 1st Inning" in text


# ---------------------------------------------- slate separation

def _any_pick(sport, grade="A+", edge="0.08"):
    return Pick(
        sport=sport, market_type="ML", selection="X",
        line=Line(odds=-110),
        edge=Decimal(edge), kelly=Decimal("0.04"),
        grade=grade, game_id=f"{sport}-1",
    )


def test_filter_daily_edge_drops_kbo_and_npb():
    picks = [
        _any_pick("MLB"),
        _any_pick("KBO"),
        _any_pick("NPB"),
        _any_pick("Soccer"),
        _any_pick("NHL"),
    ]
    out = PostingFormatter.filter_daily_edge(picks)
    sports = {p.sport for p in out}
    assert "KBO" not in sports
    assert "NPB" not in sports
    assert "Soccer" not in sports
    assert sports == {"MLB", "NHL"}


def test_filter_domestic_is_inverse_of_filter_overseas():
    picks = [_any_pick(s) for s in ("MLB", "KBO", "NFL", "NPB", "Soccer")]
    dom = {p.sport for p in PostingFormatter.filter_domestic(picks)}
    ovr = {p.sport for p in PostingFormatter.filter_overseas(picks)}
    assert dom.isdisjoint(ovr)


# ---------------------------------------------- MVS substantive Read

def test_mvs_block_substantive_read_when_engine_did_not_stash():
    """Even without engine-supplied read_notes the MVS block must
    produce a Read citing the four trigger conditions."""
    tagged = [{
        "sport": "MLB", "market_type": "ML", "selection": "NYY",
        "line": {"odds": -110, "number": None},
        "fair_prob": "0.62", "edge": "0.15", "kelly": "0.065",
        "metadata": {
            MVS_META_KEY: True,
            "mc_stability": {"stdev": "0.052", "p10": "0.56", "p90": "0.65"},
        },
    }]
    out = "\n".join(_render_mvs_block(tagged))
    assert "All four MVS thresholds cleared" in out
    assert "edge" in out.lower()
    assert "kelly" in out.lower()
    assert "MC σ" in out or "sigma" in out.lower()


def test_mvs_block_prefers_engine_read_notes_when_present():
    tagged = [{
        "sport": "MLB", "market_type": "ML", "selection": "NYY",
        "line": {"odds": -110, "number": None},
        "fair_prob": "0.62", "edge": "0.15", "kelly": "0.065",
        "metadata": {
            MVS_META_KEY: True,
            "read_notes": "Cole 2.95 vs Houck 4.10; Yankees 8-3 L11.",
        },
    }]
    out = "\n".join(_render_mvs_block(tagged))
    assert "Cole 2.95 vs Houck 4.10" in out
    # Does not bolt the boilerplate onto engine-supplied prose.
    assert "All four MVS thresholds cleared" not in out
