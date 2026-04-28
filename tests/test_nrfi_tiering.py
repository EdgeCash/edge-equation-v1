"""Tests for the shared tier classifier (Phase 3).

Pure-Python — exercises the policy directly. Tier semantics are
locked in via the post-audit thread; these tests are the contract.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Tier enum semantics
# ---------------------------------------------------------------------------


def test_tier_enum_values():
    from edge_equation.engines.tiering import Tier
    assert Tier.LOCK.value == "LOCK"
    assert Tier.STRONG.value == "STRONG"
    assert Tier.MODERATE.value == "MODERATE"
    assert Tier.LEAN.value == "LEAN"
    assert Tier.NO_PLAY.value == "NO_PLAY"


def test_is_qualifying_includes_lean_and_above():
    from edge_equation.engines.tiering import Tier
    assert Tier.LOCK.is_qualifying
    assert Tier.STRONG.is_qualifying
    assert Tier.MODERATE.is_qualifying
    assert Tier.LEAN.is_qualifying
    assert not Tier.NO_PLAY.is_qualifying


def test_is_betting_tier_excludes_lean_and_no_play():
    """LEAN is content-only per the audit; NO_PLAY obviously isn't bet."""
    from edge_equation.engines.tiering import Tier
    assert Tier.LOCK.is_betting_tier
    assert Tier.STRONG.is_betting_tier
    assert Tier.MODERATE.is_betting_tier
    assert not Tier.LEAN.is_betting_tier
    assert not Tier.NO_PLAY.is_betting_tier


# ---------------------------------------------------------------------------
# NRFI / YRFI raw-probability ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("market_type", ["NRFI", "YRFI"])
@pytest.mark.parametrize("prob,expected_tier", [
    # LOCK: ≥70%
    (0.85, "LOCK"),
    (0.70, "LOCK"),
    # STRONG: 64-69%
    (0.69, "STRONG"),
    (0.64, "STRONG"),
    # MODERATE: 58-63%
    (0.63, "MODERATE"),
    (0.58, "MODERATE"),
    # LEAN: 55-57%
    (0.57, "LEAN"),
    (0.55, "LEAN"),
    # NO_PLAY: <55%
    (0.54, "NO_PLAY"),
    (0.50, "NO_PLAY"),
    (0.10, "NO_PLAY"),
])
def test_classify_nrfi_yrfi_ladder(market_type, prob, expected_tier):
    from edge_equation.engines.tiering import classify_tier
    clf = classify_tier(market_type=market_type, side_probability=prob)
    assert clf.tier.value == expected_tier
    assert clf.basis == "raw_probability"
    assert clf.value == pytest.approx(prob)


def test_nrfi_classifier_requires_probability():
    from edge_equation.engines.tiering import classify_tier
    with pytest.raises(ValueError, match="side_probability"):
        classify_tier(market_type="NRFI")


# ---------------------------------------------------------------------------
# Edge ladder (props / full-game)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("market_type", ["ML", "Total", "Run_Line", "HR", "K"])
@pytest.mark.parametrize("edge,expected_tier", [
    (0.12,  "LOCK"),
    (0.08,  "LOCK"),
    (0.07,  "STRONG"),
    (0.05,  "STRONG"),
    (0.04,  "MODERATE"),
    (0.03,  "MODERATE"),
    (0.02,  "LEAN"),
    (0.01,  "LEAN"),
    (0.005, "NO_PLAY"),
    (0.0,   "NO_PLAY"),
    (-0.05, "NO_PLAY"),  # negative edge → NO_PLAY
])
def test_classify_edge_ladder(market_type, edge, expected_tier):
    from edge_equation.engines.tiering import classify_tier
    clf = classify_tier(market_type=market_type, edge=edge)
    assert clf.tier.value == expected_tier
    assert clf.basis == "edge"


def test_edge_classifier_requires_edge():
    from edge_equation.engines.tiering import classify_tier
    with pytest.raises(ValueError, match="edge"):
        classify_tier(market_type="ML", side_probability=0.65)


# ---------------------------------------------------------------------------
# Symmetric vs non-symmetric routing
# ---------------------------------------------------------------------------


def test_symmetric_first_inning_markets_set():
    """The set of markets that route to the prob ladder must include
    NRFI and YRFI — and should NOT include any non-symmetric markets."""
    from edge_equation.engines.tiering import SYMMETRIC_FIRST_INNING_MARKETS
    assert "NRFI" in SYMMETRIC_FIRST_INNING_MARKETS
    assert "YRFI" in SYMMETRIC_FIRST_INNING_MARKETS
    assert "ML" not in SYMMETRIC_FIRST_INNING_MARKETS
    assert "Total" not in SYMMETRIC_FIRST_INNING_MARKETS


# ---------------------------------------------------------------------------
# Tier → operator policy
# ---------------------------------------------------------------------------


def test_kelly_multiplier_per_tier():
    """Per the audit: LOCK 0.5–1×, STRONG 0.25–0.5×, MODERATE 0.10–0.25×.
    LEAN and NO_PLAY are 0 (content-only / no bet)."""
    from edge_equation.engines.tiering import Tier, kelly_multiplier
    assert 0.5 <= kelly_multiplier(Tier.LOCK) <= 1.0
    assert 0.25 <= kelly_multiplier(Tier.STRONG) <= 0.5
    assert 0.10 <= kelly_multiplier(Tier.MODERATE) <= 0.25
    assert kelly_multiplier(Tier.LEAN) == 0.0
    assert kelly_multiplier(Tier.NO_PLAY) == 0.0


def test_tier_to_grade_mapping_aligns_with_confidence_scorer():
    """LOCK→A+, STRONG→A, MODERATE→B, LEAN→C, NO_PLAY→F.
    Single grading system across the engine."""
    from edge_equation.engines.tiering import Tier, tier_to_grade
    assert tier_to_grade(Tier.LOCK) == "A+"
    assert tier_to_grade(Tier.STRONG) == "A"
    assert tier_to_grade(Tier.MODERATE) == "B"
    assert tier_to_grade(Tier.LEAN) == "C"
    assert tier_to_grade(Tier.NO_PLAY) == "F"


def test_color_hex_per_tier_returns_valid_hex():
    """LOCK → deep green, NO_PLAY → deep red, etc. — must be #rrggbb."""
    from edge_equation.engines.tiering import Tier, color_hex
    for tier in Tier:
        h = color_hex(tier)
        assert h.startswith("#")
        assert len(h) == 7
        int(h[1:], 16)  # parses as hex


# ---------------------------------------------------------------------------
# TierClassification carries the band info for caption rendering
# ---------------------------------------------------------------------------


def test_classification_band_lower_matches_threshold():
    """The classification's band_lower must equal the threshold the
    value cleared — so the email can render '65.4% (STRONG, 64-70%)'."""
    from edge_equation.engines.tiering import classify_tier
    clf = classify_tier(market_type="NRFI", side_probability=0.654)
    assert clf.tier.value == "STRONG"
    assert clf.band_lower == pytest.approx(0.64)
    # Band upper is the next-tier-up threshold (LOCK at 0.70).
    assert clf.band_upper == pytest.approx(0.70)


def test_classification_top_tier_band_upper_is_inf():
    from edge_equation.engines.tiering import classify_tier
    import math
    clf = classify_tier(market_type="NRFI", side_probability=0.85)
    assert clf.tier.value == "LOCK"
    assert math.isinf(clf.band_upper)
