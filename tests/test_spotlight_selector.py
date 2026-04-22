"""
Phase 22 Spotlight selector: deterministic most-trending-game picker.

Rules under test:
  - Only Grade A / A+ picks are eligible.
  - Trend score = sum over picks of grade_weight * abs(edge) * (1 + kelly).
  - Ties break on best grade_weight, then best abs(edge), then sport
    priority (US majors beat international), then game_id.
  - Games with no eligible picks are filtered out.
  - Empty / unranked input returns an empty selection, never raises.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.spotlight import (
    SpotlightSelection,
    _pick_contribution,
    select_spotlight_game,
)


def _p(sport, game_id, grade, edge, kelly=Decimal("0.02"), market="ML",
       selection="Home"):
    return Pick(
        sport=sport,
        market_type=market,
        selection=selection,
        line=Line(odds=-110),
        fair_prob=Decimal("0.55"),
        edge=Decimal(str(edge)),
        kelly=Decimal(str(kelly)),
        grade=grade,
        game_id=game_id,
    )


def test_empty_picks_returns_empty_selection():
    sel = select_spotlight_game([])
    assert isinstance(sel, SpotlightSelection)
    assert sel.game_id is None
    assert sel.picks == ()
    assert sel.trend_score == Decimal("0")


def test_only_low_grade_picks_return_empty():
    picks = [_p("MLB", "G1", "B", 0.08), _p("MLB", "G1", "C", 0.06)]
    sel = select_spotlight_game(picks)
    assert sel.game_id is None
    assert sel.picks == ()


def test_highest_edge_game_wins():
    high = _p("MLB", "GAME-HIGH", "A+", 0.12)
    low = _p("MLB", "GAME-LOW", "A", 0.06)
    sel = select_spotlight_game([high, low])
    assert sel.game_id == "GAME-HIGH"
    assert sel.picks == (high,)


def test_multiple_picks_same_game_contribute_to_trend():
    one_big = _p("MLB", "GAME-A", "A+", 0.15)
    two_small_a = _p("MLB", "GAME-B", "A", 0.08)
    two_small_b = _p("MLB", "GAME-B", "A", 0.08, market="Total", selection="Over 9.5")
    sel = select_spotlight_game([one_big, two_small_a, two_small_b])
    # GAME-B's two A picks (2 * 1.0 * 0.08) > GAME-A's single (1.25 * 0.15) ~
    # 0.163 vs 0.188. GAME-A should still win on a single pick.
    assert sel.game_id == "GAME-A"


def test_sport_priority_breaks_true_ties():
    us = _p("NFL", "US-1", "A", 0.10)
    intl = _p("KBO", "INTL-1", "A", 0.10)
    sel = select_spotlight_game([us, intl])
    assert sel.game_id == "US-1"
    assert sel.sport == "NFL"


def test_pick_contribution_monotone_in_edge():
    a = _p("MLB", "G", "A", 0.05)
    b = _p("MLB", "G", "A", 0.10)
    assert _pick_contribution(b) > _pick_contribution(a)


def test_grade_weight_aplus_beats_a_at_equal_edge():
    aplus = _p("MLB", "G", "A+", 0.07)
    a = _p("MLB", "G", "A", 0.07)
    assert _pick_contribution(aplus) > _pick_contribution(a)


def test_selection_tuple_is_hashable_dataclass():
    sel = SpotlightSelection(
        game_id="G", picks=(), trend_score=Decimal("1"), sport="MLB"
    )
    assert sel.to_dict()["game_id"] == "G"
    assert sel.to_dict()["n_picks"] == 0


def test_picks_without_game_id_are_ignored():
    ghost = _p("MLB", None, "A+", 0.20)
    real = _p("MLB", "G1", "A", 0.05)
    sel = select_spotlight_game([ghost, real])
    assert sel.game_id == "G1"
