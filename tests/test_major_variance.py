"""
Major Variance Signal: rare, premium-only, credibility-first detector.

Trigger requires ALL FOUR:
  - grade == "A+"
  - edge >= 0.12
  - kelly >= 0.05
  - mc stability: stdev < 0.09 OR (p90 - p10) < 0.12

Any missing piece -> does NOT fire. Public-mode MUST strip the flag so
it never appears in free content.
"""
from decimal import Decimal

import pytest

from edge_equation.engine.major_variance import (
    META_KEY,
    META_REASON_KEY,
    MIN_EDGE,
    MIN_KELLY,
    SIGNAL_LABEL,
    detect,
    is_tagged,
    tag_pick,
)
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import format_premium_daily


def _pick(grade="A+", edge="0.14", kelly="0.07", meta=None):
    return Pick(
        sport="MLB", market_type="ML", selection="Home",
        line=Line(odds=-110),
        fair_prob=Decimal("0.60"),
        edge=Decimal(edge) if edge is not None else None,
        kelly=Decimal(kelly) if kelly is not None else None,
        grade=grade,
        game_id="G1",
        metadata=meta or {},
    )


# ------------------------------------------------ constants audit

def test_thresholds_are_strict_and_match_brand_spec():
    # If someone softens these without bumping the docs, the brand
    # contract silently erodes. Pin the numbers in a test.
    assert MIN_EDGE == Decimal("0.12")
    assert MIN_KELLY == Decimal("0.05")
    assert SIGNAL_LABEL == "Major Variance Signal"


# ------------------------------------------------ detector -- all pass

def test_fires_when_all_four_conditions_met():
    p = _pick()
    sig = detect(p, mc_stability={"stdev": 0.07})
    assert sig.fires is True
    assert "all four thresholds satisfied" in sig.reason


def test_fires_via_tight_p10_p90_band_when_stdev_missing():
    p = _pick()
    sig = detect(p, mc_stability={"p10": 0.54, "p90": 0.64})
    assert sig.fires is True   # band = 0.10 < 0.12


# ------------------------------------------------ detector -- credibility first

def test_does_not_fire_when_grade_is_not_a_plus():
    for bad_grade in ("A", "B", "C", "D", "F"):
        p = _pick(grade=bad_grade)
        sig = detect(p, mc_stability={"stdev": 0.05})
        assert sig.fires is False
        assert "grade" in sig.reason


def test_does_not_fire_when_edge_below_threshold():
    p = _pick(edge="0.11")
    sig = detect(p, mc_stability={"stdev": 0.05})
    assert sig.fires is False
    assert "edge" in sig.reason


def test_does_not_fire_when_kelly_below_threshold():
    p = _pick(kelly="0.049")
    sig = detect(p, mc_stability={"stdev": 0.05})
    assert sig.fires is False
    assert "kelly" in sig.reason


def test_does_not_fire_when_mc_stdev_too_high():
    p = _pick()
    sig = detect(p, mc_stability={"stdev": 0.10})
    assert sig.fires is False
    assert "stability" in sig.reason


def test_does_not_fire_when_mc_band_too_wide_and_stdev_absent():
    p = _pick()
    sig = detect(p, mc_stability={"p10": 0.40, "p90": 0.60})  # band=0.20
    assert sig.fires is False


def test_does_not_fire_when_mc_stability_entirely_missing():
    """Credibility-first: no MC evidence -> no signal, even on a
    'perfect on paper' pick. A half-baked trigger would erode meaning."""
    p = _pick()
    sig = detect(p, mc_stability=None)
    assert sig.fires is False
    assert "credibility-first" in sig.reason


def test_does_not_fire_when_edge_or_kelly_none():
    sig = detect(_pick(edge=None))
    assert sig.fires is False
    sig = detect(_pick(kelly=None))
    assert sig.fires is False


# ------------------------------------------------ tag / is_tagged

def test_tag_pick_sets_metadata_flag_on_firing_signal():
    p = _pick()
    sig = detect(p, mc_stability={"stdev": 0.05})
    tagged = tag_pick(p, sig)
    assert is_tagged(tagged) is True
    assert tagged.metadata[META_KEY] is True
    # Other Pick fields round-trip unchanged
    assert tagged.grade == p.grade
    assert tagged.edge == p.edge


def test_tag_pick_records_reason_even_when_not_firing():
    p = _pick(edge="0.09")
    sig = detect(p, mc_stability={"stdev": 0.05})
    tagged = tag_pick(p, sig)
    assert is_tagged(tagged) is False
    assert tagged.metadata[META_REASON_KEY]   # reason still recorded
    assert tagged.metadata[META_KEY] is False


def test_is_tagged_false_on_untagged_pick():
    assert is_tagged(_pick()) is False


def test_detect_reads_mc_stability_from_pick_metadata():
    p = _pick(meta={"mc_stability": {"stdev": 0.05}})
    sig = detect(p, mc_stability=None)   # caller didn't pass one
    assert sig.fires is True


# ------------------------------------------------ betting engine integration

def test_betting_engine_tags_mvs_when_mc_stability_supplied():
    from edge_equation.engine.betting_engine import BettingEngine
    from edge_equation.engine.feature_builder import FeatureBuilder
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 3.0, "strength_away": 1.0, "home_adv": 0.1},
        universal_features={"home_edge": 0.3},
        game_id="G1", selection="Home",
    )
    # The concentrated strengths push edge high. Supply MC stability so
    # the detector has data to decide on.
    pick = BettingEngine.evaluate(
        bundle, Line(odds=-110),
        mc_stability={"stdev": 0.05, "p10": 0.55, "p90": 0.63},
    )
    if pick.grade == "A+" and pick.edge is not None and pick.edge >= MIN_EDGE:
        assert pick.metadata.get(META_KEY) is True
    else:
        # If the input didn't actually produce an A+ edge, the tag must
        # NOT be set (credibility-first).
        assert pick.metadata.get(META_KEY) is False


def test_betting_engine_does_not_tag_in_public_mode():
    """public_mode runs MUST NOT carry the MVS flag: premium-only signal."""
    from edge_equation.engine.betting_engine import BettingEngine
    from edge_equation.engine.feature_builder import FeatureBuilder
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 3.0, "strength_away": 1.0, "home_adv": 0.1},
        universal_features={"home_edge": 0.3},
        game_id="G1", selection="Home",
    )
    pick = BettingEngine.evaluate(
        bundle, Line(odds=-110), public_mode=True,
        mc_stability={"stdev": 0.02},
    )
    assert is_tagged(pick) is False


# ------------------------------------------------ premium render

def test_premium_body_shows_mvs_section_when_any_pick_fires():
    firing = _pick(meta={META_KEY: True, "read_notes": "Alignment confirmed."})
    card = PostingFormatter.build_card(
        card_type="premium_daily", picks=[firing],
        generated_at="2026-04-22T11:00:00",
    )
    body = format_premium_daily(card)
    assert "MAJOR VARIANCE SIGNAL" in body
    # Transparent intro note must be present on first occurrence
    assert "rare Major Variance Signal" in body
    # Factual stat line
    assert "EE Projection: Grade A+" in body
    assert "Kelly Suggestion:" in body


def test_premium_body_omits_mvs_section_when_no_pick_fires():
    quiet = _pick(meta={META_KEY: False})
    card = PostingFormatter.build_card(
        card_type="premium_daily", picks=[quiet],
        generated_at="2026-04-22T11:00:00",
    )
    body = format_premium_daily(card)
    assert "MAJOR VARIANCE SIGNAL" not in body
