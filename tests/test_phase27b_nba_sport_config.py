"""
Phase 27b -- NBA added to SPORT_CONFIG + strength_blend fallback.

Triggered by a real production crash from the nightly settler. The
Results Settler (Phase 27) backfilled NBA game results, then the
next slate-build call into FeatureComposer.enrich_markets -> Team-
StrengthBuilder.build -> SportConfig.strength_blend("NBA") raised
KeyError: 'Unknown sport: NBA'. Only MLB/KBO/NPB/NFL/NHL/Soccer/
NCAA_* were configured.

This test module guarantees:
  - NBA has a full SPORT_CONFIG entry (all require-keys resolve).
  - SportConfig.strength_blend falls back to a neutral pyth/form/elo
    split for any unknown sport rather than raising. A missing sport
    produces a generic strength estimate instead of crashing the
    slate builder -- the engine still grades picks on whatever
    markets arrive, just without sport-specific tuning.
"""
from decimal import Decimal

import pytest

from edge_equation.config.sport_config import SPORT_CONFIG, SportConfig


# ------------------------------------------------ NBA entry complete


_NBA_REQUIRED_KEYS = (
    "markets",
    "league_baseline_total",
    "ml_universal_weight",
    "prop_universal_weight",
    "pythagorean_exponent",
    "decay_lambda",
    "form_window_games",
    "home_adv",
    "strength_blend",
)


def test_nba_is_in_sport_config():
    assert "NBA" in SPORT_CONFIG


@pytest.mark.parametrize("key", _NBA_REQUIRED_KEYS)
def test_nba_has_every_required_config_key(key):
    assert key in SPORT_CONFIG["NBA"], (
        f"NBA missing SPORT_CONFIG key {key!r} -- any engine path that "
        f"calls SportConfig.require(..., {key!r}) will crash"
    )


def test_nba_pythagorean_exponent_sensible():
    # Basketball Pythagorean typically lands in the 13-15 range;
    # anchoring the test loosely at >= 10 and <= 20 catches an
    # accidental copy-paste from the much lower baseball value.
    exp = SportConfig.pythagorean_exponent("NBA")
    assert Decimal("10") < exp < Decimal("20")


def test_nba_home_adv_within_basketball_range():
    # Long-run NBA home-court edge sits near 0.15 (~60% win rate).
    adv = SportConfig.home_adv("NBA")
    assert Decimal("0.08") <= adv <= Decimal("0.20")


def test_nba_strength_blend_sums_to_one():
    blend = SportConfig.strength_blend("NBA")
    total = sum(blend.values())
    assert Decimal("0.99") <= total <= Decimal("1.01")


def test_nba_markets_include_primary_three():
    markets = SportConfig.require("NBA", "markets")
    assert {"ML", "Spread", "Total"}.issubset(set(markets))


# ------------------------------------------------ fallback for unknown sports


def test_strength_blend_unknown_sport_returns_neutral_default():
    """Fallback is the guard -- future sports that land in game_results
    before SPORT_CONFIG catches up must NOT crash the slate builder."""
    blend = SportConfig.strength_blend("MADE_UP_SPORT")
    assert set(blend.keys()) == {"pyth", "form", "elo", "pitching"}
    assert sum(blend.values()) == Decimal("1.00")
    # Neutral defaults favor pyth + elo + form; pitching zero since
    # only baseball-family sports use it.
    assert blend["pitching"] == Decimal("0")


def test_strength_blend_known_sport_still_returns_sport_specific_value():
    """Regression guard: the fallback path must NOT override configured
    sports. MLB keeps its full blend including a nonzero pitching
    weight."""
    blend = SportConfig.strength_blend("MLB")
    # Pre-existing MLB blend includes pitching; fallback does not.
    assert blend["pitching"] > Decimal("0")


# ------------------------------------------------ downstream integration guard


def test_team_strength_builder_accepts_nba_without_raising():
    """End-to-end: FeatureComposer -> TeamStrengthBuilder -> SportConfig
    was the crash path. With NBA now configured + the fallback in
    place, an NBA slate build must complete without a KeyError even
    on empty results."""
    from edge_equation.stats.team_strength import TeamStrengthBuilder
    # Empty results still exercises the code path. We just want it
    # to NOT raise.
    result = TeamStrengthBuilder.build(
        team="LAL", league="NBA", results=[],
    )
    # We don't assert a specific value -- the contract is "doesn't
    # crash" + "returns a usable TeamStrength"; the math is covered
    # by existing team_strength tests.
    assert result is not None
