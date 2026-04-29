"""Tests for the same-game / same-player correlation lookup."""

from __future__ import annotations

import pytest

from edge_equation.engines.parlay.correlations import (
    ParlayLegContext,
    are_mutually_exclusive,
    correlation_for_pair,
)


def _ctx(market, side, *, game=None, player=None):
    return ParlayLegContext(market_type=market, side=side,
                              game_id=game, player_id=player)


# ---------------------------------------------------------------------------
# Default — independence for unrelated legs
# ---------------------------------------------------------------------------


def test_independent_default_for_different_games():
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("ML", "Yankees ML", game="g2")
    assert correlation_for_pair(a, b) == 0.0


def test_independent_when_no_table_entry():
    """Two markets neither table covers → 0.0, not a crash."""
    a = _ctx("Special", "Yes", game="g1")
    b = _ctx("AnotherSpecial", "Yes", game="g1")
    assert correlation_for_pair(a, b) == 0.0


# ---------------------------------------------------------------------------
# Same-game pairs
# ---------------------------------------------------------------------------


def test_nrfi_total_under_positive_when_same_game():
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("Total", "Under 8.5", game="g1")
    assert correlation_for_pair(a, b) == pytest.approx(0.30)


def test_nrfi_total_under_zero_when_different_games():
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("Total", "Under 8.5", game="g2")
    assert correlation_for_pair(a, b) == 0.0


def test_nrfi_total_over_flips_sign():
    """NRFI is a 'low' direction; Total Over is 'high'. Sign flips."""
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("Total", "Over 8.5", game="g1")
    assert correlation_for_pair(a, b) == pytest.approx(-0.30)


def test_f5_total_drives_full_game_total_strongly():
    a = _ctx("F5_Total", "Under 4.5", game="g1")
    b = _ctx("Total", "Under 8.5", game="g1")
    assert correlation_for_pair(a, b) == pytest.approx(0.55)


def test_ml_runline_strongly_correlated_same_team():
    """ML and Run_Line on the same side are mostly the same bet."""
    a = _ctx("ML", "Yankees ML", game="g1")
    b = _ctx("Run_Line", "Yankees -1.5", game="g1")
    assert correlation_for_pair(a, b) == pytest.approx(0.70)


# ---------------------------------------------------------------------------
# Same-player pairs
# ---------------------------------------------------------------------------


def test_hr_total_bases_strongly_positive_same_player():
    a = _ctx("HR", "Yes", game="g1", player="p1")
    b = _ctx("Total_Bases", "Over 1.5", game="g1", player="p1")
    assert correlation_for_pair(a, b) == pytest.approx(0.60)


def test_hits_k_negative_same_player():
    """A hitter's Hits and K's prop are negatively correlated."""
    a = _ctx("Hits", "Over 1.5", game="g1", player="p1")
    b = _ctx("K", "Yes", game="g1", player="p1")
    assert correlation_for_pair(a, b) == pytest.approx(-0.40)


def test_hits_k_zero_when_different_players():
    a = _ctx("Hits", "Over 1.5", game="g1", player="p1")
    b = _ctx("K", "Yes", game="g1", player="p2")
    assert correlation_for_pair(a, b) == 0.0


# ---------------------------------------------------------------------------
# Mutual exclusion
# ---------------------------------------------------------------------------


def test_nrfi_yrfi_same_game_are_mutually_exclusive():
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("YRFI", "Over 0.5", game="g1")
    assert are_mutually_exclusive(a, b) is True


def test_nrfi_yrfi_different_games_not_mutually_exclusive():
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("YRFI", "Over 0.5", game="g2")
    assert are_mutually_exclusive(a, b) is False


def test_correlation_for_mutually_exclusive_returns_minus_one():
    """Defensive: if the builder accidentally pairs them, the MC must
    see a hard ρ that flags the impossibility."""
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("YRFI", "Over 0.5", game="g1")
    assert correlation_for_pair(a, b) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Symmetry
# ---------------------------------------------------------------------------


def test_correlation_lookup_is_symmetric():
    """ρ(a, b) == ρ(b, a) for every pair."""
    a = _ctx("NRFI", "Under 0.5", game="g1")
    b = _ctx("F5_Total", "Under 4.5", game="g1")
    assert correlation_for_pair(a, b) == correlation_for_pair(b, a)
