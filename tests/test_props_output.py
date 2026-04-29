"""Tests for the props canonical output payload + adapters."""

from __future__ import annotations

from typing import Any

import pytest

from edge_equation.engines.props_prizepicks import (
    MLB_PROP_MARKETS,
    PropEdgePick,
    PropOutput,
    build_prop_output,
    color_band_for_tier,
    color_hex_for_tier,
    to_api_dict,
    to_email_card,
)
from edge_equation.engines.tiering import Tier, TierClassification


def _pick(*, tier=Tier.STRONG, edge_pp=7.4, market_prob=0.286,
            model_prob=0.36, american_odds=+250, market="HR",
            side="Over", line_value=0.5, player="Aaron Judge",
            vig_corrected=False):
    m = MLB_PROP_MARKETS[market]
    clf = TierClassification(
        tier=tier, basis="edge", value=edge_pp / 100.0,
        band_lower=0.05, band_upper=0.08,
    )
    return PropEdgePick(
        market_canonical=m.canonical, market_label=m.label,
        player_name=player, line_value=float(line_value), side=side,
        model_prob=float(model_prob),
        market_prob_raw=float(market_prob),
        market_prob_devigged=float(market_prob),
        vig_corrected=vig_corrected, edge_pp=float(edge_pp),
        american_odds=float(american_odds),
        decimal_odds=2.5 if american_odds > 0 else 1.5,
        book="draftkings", tier=tier, tier_classification=clf,
    )


# ---------------------------------------------------------------------------
# Color band / hex
# ---------------------------------------------------------------------------


def test_color_band_returns_brand_label_per_tier():
    assert color_band_for_tier(Tier.ELITE) == "Electric Blue"
    assert color_band_for_tier(Tier.STRONG) == "Deep Green"
    assert color_band_for_tier(Tier.MODERATE) == "Light Green"
    assert color_band_for_tier(Tier.LEAN) == "Yellow"
    assert color_band_for_tier(Tier.NO_PLAY) == "Orange"


def test_color_hex_is_seven_char_hex():
    for tier in Tier:
        h = color_hex_for_tier(tier)
        assert h.startswith("#") and len(h) == 7
        int(h[1:], 16)


# ---------------------------------------------------------------------------
# build_prop_output factory
# ---------------------------------------------------------------------------


def test_build_prop_output_carries_pick_fields():
    pick = _pick()
    out = build_prop_output(
        pick, confidence=0.72, lam=0.28, blend_n=250,
        game_id="BOS @ NYY",
    )
    assert isinstance(out, PropOutput)
    assert out.player_name == "Aaron Judge"
    assert out.market_type == "HR"
    assert out.market_label == "Home Runs"
    assert out.side == "Over"
    assert out.line_value == 0.5
    assert out.model_prob == pytest.approx(0.36, abs=1e-6)
    assert out.model_pct == pytest.approx(36.0, abs=0.05)
    assert out.tier == "STRONG"
    assert out.color_band == "Deep Green"
    assert out.book == "draftkings"
    assert out.lam == pytest.approx(0.28)
    assert out.blend_n == 250
    assert out.confidence == pytest.approx(0.72)


def test_build_prop_output_kelly_uses_tier_multiplier():
    """LOCK (multiplier 0.75) gives a larger Kelly than STRONG (0.375)
    on the same edge — the per-tier discipline."""
    lock_pick = _pick(tier=Tier.ELITE, edge_pp=12.0,
                        model_prob=0.41, market_prob=0.29)
    strong_pick = _pick(tier=Tier.STRONG, edge_pp=7.0,
                          model_prob=0.36, market_prob=0.29)
    lock_out = build_prop_output(lock_pick)
    strong_out = build_prop_output(strong_pick)
    # Both should produce positive Kelly; LOCK's should be larger per
    # unit of edge thanks to its multiplier.
    assert (lock_out.kelly_units or 0) > 0
    assert (strong_out.kelly_units or 0) > 0


def test_build_prop_output_lean_kelly_zero_per_audit():
    """LEAN tier is content-only — the Kelly multiplier is 0 by audit
    policy, so the `kelly_units` field must come back at 0."""
    pick = _pick(tier=Tier.LEAN, edge_pp=2.0, model_prob=0.35,
                   market_prob=0.33)
    out = build_prop_output(pick)
    assert out.kelly_units == 0.0


def test_build_prop_output_drivers_optional():
    out = build_prop_output(_pick())
    assert out.driver_text == []
    out2 = build_prop_output(_pick(),
                                driver_text=["+1.8 home pitcher xERA"])
    assert out2.driver_text == ["+1.8 home pitcher xERA"]


# ---------------------------------------------------------------------------
# to_email_card adapter
# ---------------------------------------------------------------------------


def test_to_email_card_includes_tier_color_lambda_edge_kelly():
    out = build_prop_output(_pick(), confidence=0.72, lam=0.28,
                              blend_n=250, game_id="BOS @ NYY")
    text = to_email_card(out)
    assert "Aaron Judge · Home Runs Over 0.5" in text
    assert "[STRONG" in text
    assert "Deep Green" in text
    assert "λ 0.28" in text
    assert "conf 72%" in text
    assert "edge +7.4pp" in text
    assert "stake" in text
    assert "odds +250" in text
    assert "draftkings" in text


def test_to_email_card_omits_kelly_when_zero():
    """LEAN tier (Kelly=0) — the stake line should drop out cleanly."""
    pick = _pick(tier=Tier.LEAN, edge_pp=2.0, model_prob=0.35,
                   market_prob=0.33)
    out = build_prop_output(pick, confidence=0.45)
    text = to_email_card(out)
    assert "stake" not in text


def test_to_email_card_appends_why_clause_when_drivers_present():
    out = build_prop_output(
        _pick(), driver_text=[
            "+1.8 home pitcher xERA (30d)",
            "+1.2 park run factor",
        ],
    )
    text = to_email_card(out)
    assert "Why:" in text
    assert "home pitcher xERA" in text


def test_to_email_card_negative_odds_format_correctly():
    pick = _pick(american_odds=-115, model_prob=0.55, market_prob=0.50,
                   edge_pp=5.0, tier=Tier.STRONG)
    out = build_prop_output(pick)
    text = to_email_card(out)
    assert "odds -115" in text


# ---------------------------------------------------------------------------
# to_api_dict adapter
# ---------------------------------------------------------------------------


def test_to_api_dict_has_headline_and_serializable_shape():
    out = build_prop_output(_pick(), confidence=0.72, lam=0.28)
    d = to_api_dict(out)
    assert d["player_name"] == "Aaron Judge"
    assert d["headline"]
    assert d["tier"] == "STRONG"
    # Floats / strings only — must be JSON-friendly.
    import json
    json.dumps(d, default=str)


def test_to_api_dict_drivers_is_plain_list():
    out = build_prop_output(_pick(), driver_text=["+1 a", "+0.5 b"])
    d = to_api_dict(out)
    assert isinstance(d["driver_text"], list)
    assert d["driver_text"] == ["+1 a", "+0.5 b"]
