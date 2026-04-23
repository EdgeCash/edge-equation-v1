"""
Phase 28 -- ML/BTTS side-aware fair_prob + edge sanity guard +
auto-populated Read + tightened parlay.

The bug: ProbabilityCalculator.calculate_fair_value returns the HOME
team's win probability. Before this fix, the engine compared that
home-prob against EVERY ML selection's odds (including the away
side's), so both sides of the same game graded with positive edge,
producing the +48% on +2200 absurdity and "both A+" pattern.

Tests below pin the new contract:
  - Same fair_prob seed: home pick edge sign opposite of away pick
    edge sign. They CANNOT both be A+.
  - +30% edge ceiling rejects impossible-positive-edge picks.
  - Selection mismatched to home/away yields fair_prob=None.
  - Read field auto-populates from feature inputs when none supplied.
  - Parlay-of-day demands A+ AND edge <= 20%.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.betting_engine import (
    BettingEngine,
    _MAX_REASONABLE_EDGE,
    _baseline_read,
    _resolve_selection_side,
)
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.posting_formatter import PostingFormatter


def _bundle(selection: str, home_team: str, away_team: str,
            sh: float = 1.32, sa: float = 1.15):
    return FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": sh, "strength_away": sa, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id=f"MLB-{home_team}-{away_team}",
        selection=selection,
        metadata={"home_team": home_team, "away_team": away_team},
    )


# ------------------------------------------------ side resolution


def test_resolve_side_home_match():
    assert _resolve_selection_side("ML", "NYY", "NYY", "BOS") == "home"


def test_resolve_side_away_match():
    assert _resolve_selection_side("ML", "BOS", "NYY", "BOS") == "away"


def test_resolve_side_unknown_returns_none():
    assert _resolve_selection_side("ML", "TBD", "NYY", "BOS") is None


def test_resolve_side_btts_yes_no():
    assert _resolve_selection_side("BTTS", "Yes", "NYY", "BOS") == "home"
    assert _resolve_selection_side("BTTS", "No",  "NYY", "BOS") == "away"


# ------------------------------------------------ both-sides invariant


def test_both_sides_of_same_game_have_inverse_fair_probs():
    """The home pick and away pick on the same matchup must have
    fair_probs that sum to 1.0. Pre-Phase-28 they were both equal
    to the home prob -- the bug pattern."""
    home_pick = BettingEngine.evaluate(
        _bundle("NYY", "NYY", "BOS"), Line(odds=-150),
    )
    away_pick = BettingEngine.evaluate(
        _bundle("BOS", "NYY", "BOS"), Line(odds=+130),
    )
    total = home_pick.fair_prob + away_pick.fair_prob
    assert (Decimal("0.999") < total < Decimal("1.001")), (
        f"home + away fair_prob must sum to 1, got {total}"
    )


def test_both_sides_cannot_both_grade_aplus():
    """Mathematical impossibility post-fix: edge_home + edge_away =
    1.0 - (implied_home + implied_away) which is at most ~0.10 (the
    book's hold). So one side's edge must be <= 0.05 -- not A+."""
    home_pick = BettingEngine.evaluate(
        _bundle("NYY", "NYY", "BOS"), Line(odds=-150),
    )
    away_pick = BettingEngine.evaluate(
        _bundle("BOS", "NYY", "BOS"), Line(odds=+130),
    )
    aplus_count = sum(1 for p in (home_pick, away_pick) if p.grade == "A+")
    assert aplus_count <= 1, "both sides must not grade A+"


def test_selection_unknown_yields_no_grade():
    """Selection that matches neither home nor away team -> the engine
    refuses to grade rather than picking one direction at random."""
    pick = BettingEngine.evaluate(
        _bundle("CHC", "NYY", "BOS"), Line(odds=-110),
    )
    assert pick.fair_prob is None
    assert pick.edge is None
    assert pick.grade == "C"
    assert "selection 'CHC'" in (pick.metadata.get("sanity_rejected_reason") or "")


# ------------------------------------------------ +30% sanity ceiling


def test_implausible_positive_edge_is_rejected():
    """A pathologically lopsided strength differential pushes fair_prob
    to the 0.99 clamp; combined with a long-shot favorite line that
    gives a small implied prob, the resulting edge clears 30% and
    must be silently rejected (Phase 28 sanity guard)."""
    pick = BettingEngine.evaluate(
        _bundle("NYY", "NYY", "BOS", sh=10.0, sa=0.1),
        Line(odds=+200),    # implied 0.333; fair clamps high
    )
    # The engine refuses to publish.
    assert pick.edge is None
    assert pick.kelly is None
    assert pick.grade == "C"
    reason = pick.metadata.get("sanity_rejected_reason") or ""
    assert "exceeds" in reason and "sanity ceiling" in reason


def test_negative_edge_is_not_rejected_by_sanity_guard():
    """-30% edge is a legitimate "this side is overpriced" signal --
    the engine grades it D/F via ConfidenceScorer rather than dropping
    it. The guard fires only on POSITIVE absurdity."""
    pick = BettingEngine.evaluate(
        _bundle("BOS", "NYY", "BOS", sh=10.0, sa=0.1),
        Line(odds=-150),    # away dog with high implied -> negative edge
    )
    # edge is present (negative), grade is bad but it's not sanity-rejected
    assert pick.edge is not None
    assert pick.edge < Decimal("0")
    assert pick.metadata.get("sanity_rejected_reason") is None


def test_max_reasonable_edge_constant_locked_at_30pct():
    """Brand contract: 30% positive ML edge is the absolute limit of
    what we will publish. Tightening or loosening this requires an
    intentional code change + brand sign-off."""
    assert _MAX_REASONABLE_EDGE == Decimal("0.30")


# ------------------------------------------------ Read auto-population


def test_read_field_auto_populates_when_metadata_missing():
    """Phase 31: the baseline Read now requires a read_context block
    (games_used >= 5 for both sides) before it will surface a strength
    line -- a bare bundle without composer-stashed context gets the
    MC band row only. Either way the Read field is non-empty and is
    factual (no generic placeholder prose)."""
    pick = BettingEngine.evaluate(
        _bundle("NYY", "NYY", "BOS"), Line(odds=-130),
    )
    read = pick.metadata.get("read_notes") or ""
    assert read, "read_notes should be auto-populated"
    assert "no narrative delta" not in read.lower()
    assert "edge lives in the price" not in read.lower()
    assert "strengths within noise" not in read.lower()
    # MC band line from the Phase 30 wiring is always present.
    assert "MC band" in read


def test_explicit_read_notes_take_precedence_over_baseline():
    """Caller-supplied read_notes are NOT overwritten by the baseline."""
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.3, "strength_away": 1.2, "home_adv": 0.1},
        universal_features={"home_edge": 0.05},
        selection="NYY",
        metadata={
            "home_team": "NYY", "away_team": "BOS",
            "read_notes": "Bullpen rest favors home; wind out to right.",
        },
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-130))
    assert pick.metadata["read_notes"] == (
        "Bullpen rest favors home; wind out to right."
    )


def test_baseline_read_handles_missing_fields_gracefully():
    """No inputs -> read may be empty string but never raises."""
    out = _baseline_read(
        market_type="ML", selection="NYY",
        bundle=FeatureBuilder.build(
            sport="MLB", market_type="ML",
            inputs={"strength_home": 1.0, "strength_away": 1.0,
                    "home_adv": 0.0},
            universal_features={},
            selection="NYY",
            metadata={"home_team": "NYY", "away_team": "BOS"},
        ),
        fair_prob=None,
        edge=None,
        hfa_value=None,
        decay_halflife_days=None,
    )
    assert isinstance(out, str)


# ------------------------------------------------ tightened parlay


def _parlay_pick(grade="A+", edge="0.14", kelly="0.05", game_id="G"):
    return Pick(
        sport="MLB", market_type="ML", selection="X",
        line=Line(odds=-110),
        fair_prob=Decimal("0.55"),
        edge=Decimal(edge),
        kelly=Decimal(kelly),
        grade=grade,
        game_id=game_id,
    )


def test_parlay_rejects_below_aplus():
    picks = [_parlay_pick(grade="A", game_id=f"G{i}") for i in range(4)]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert legs == []


def test_parlay_rejects_legs_above_max_leg_edge():
    """Phase 28 trust restoration: any leg with edge > 20% is dropped
    even if A+. Catches the residual "implausible edge" pattern in
    case the upstream sanity guard ever misses one. Phase 30 also
    requires edge >= 0.12."""
    picks = [
        _parlay_pick(grade="A+", edge="0.25", game_id="G1"),  # too hot
        _parlay_pick(grade="A+", edge="0.18", game_id="G2"),
        _parlay_pick(grade="A+", edge="0.15", game_id="G3"),
        _parlay_pick(grade="A+", edge="0.13", game_id="G4"),
    ]
    legs = PostingFormatter.select_parlay_of_day(picks)
    # G1 dropped for excess edge; the remaining 3 form the parlay.
    assert len(legs) == 3
    assert all(p.game_id != "G1" for p in legs)


def test_parlay_admits_aplus_with_modest_edge():
    """Sanity check: well-behaved A+ legs do form a parlay."""
    picks = [_parlay_pick(grade="A+", edge="0.13", game_id=f"G{i}")
             for i in range(4)]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert 3 <= len(legs) <= 4
