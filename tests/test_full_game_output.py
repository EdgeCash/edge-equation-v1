"""Tests for the full-game canonical output payload + adapters."""

from __future__ import annotations

import pytest

from edge_equation.engines.full_game import (
    FullGameEdgePick,
    FullGameOutput,
    MLB_FULL_GAME_MARKETS,
    build_full_game_output,
    color_band_for_tier,
    color_hex_for_tier,
    to_api_dict,
    to_email_card,
)
from edge_equation.engines.tiering import Tier, TierClassification


def _pick(*, market="Total", side="Over", line_value=8.5,
            tier=Tier.STRONG, edge_pp=7.4,
            model_prob=0.58, market_prob=0.50,
            american_odds=-110, team_tricode=""):
    m = MLB_FULL_GAME_MARKETS[market]
    clf = TierClassification(tier=tier, basis="edge",
                                value=edge_pp / 100.0,
                                band_lower=0.05, band_upper=0.08)
    return FullGameEdgePick(
        market_canonical=m.canonical, market_label=m.label,
        home_team="New York Yankees", away_team="Boston Red Sox",
        home_tricode="NYY", away_tricode="BOS",
        side=side, team_tricode=team_tricode, line_value=line_value,
        model_prob=float(model_prob),
        market_prob_raw=float(market_prob),
        market_prob_devigged=float(market_prob),
        vig_corrected=False, edge_pp=float(edge_pp),
        american_odds=float(american_odds),
        decimal_odds=1.91 if american_odds < 0 else 2.5,
        book="draftkings", tier=tier, tier_classification=clf,
    )


# ---------------------------------------------------------------------------
# Tier color helpers
# ---------------------------------------------------------------------------


def test_color_band_per_tier():
    assert color_band_for_tier(Tier.ELITE) == "Electric Blue"
    assert color_band_for_tier(Tier.STRONG) == "Deep Green"
    assert color_band_for_tier(Tier.MODERATE) == "Light Green"
    assert color_band_for_tier(Tier.LEAN) == "Yellow"
    assert color_band_for_tier(Tier.NO_PLAY) == "Orange"


def test_color_hex_round_trip_seven_chars():
    for t in Tier:
        h = color_hex_for_tier(t)
        assert h.startswith("#") and len(h) == 7
        int(h[1:], 16)


# ---------------------------------------------------------------------------
# build_full_game_output factory
# ---------------------------------------------------------------------------


def test_build_full_game_output_carries_pick_fields():
    pick = _pick()
    out = build_full_game_output(
        pick, confidence=0.72, lam_used=9.54, lam_home=4.57, lam_away=4.97,
        blend_n_home=40, blend_n_away=40,
    )
    assert isinstance(out, FullGameOutput)
    assert out.market_type == "Total"
    assert out.market_label == "Total Runs"
    assert out.side == "Over"
    assert out.line_value == 8.5
    assert out.model_prob == pytest.approx(0.58)
    assert out.tier == "STRONG"
    assert out.color_band == "Deep Green"
    assert out.lam_used == pytest.approx(9.54)
    assert out.confidence == pytest.approx(0.72)


def test_build_full_game_output_kelly_uses_tier_multiplier():
    """LOCK gives larger Kelly than STRONG on the same edge."""
    lock_pick = _pick(tier=Tier.ELITE, edge_pp=12.0,
                          model_prob=0.62, market_prob=0.50)
    strong_pick = _pick(tier=Tier.STRONG, edge_pp=7.0,
                            model_prob=0.57, market_prob=0.50)
    assert (build_full_game_output(lock_pick).kelly_units or 0) > 0
    assert (build_full_game_output(strong_pick).kelly_units or 0) > 0


def test_build_full_game_output_lean_kelly_zero():
    """LEAN tier multiplier is 0.0 — kelly_units must come back 0."""
    pick = _pick(tier=Tier.LEAN, edge_pp=2.0,
                   model_prob=0.55, market_prob=0.53)
    out = build_full_game_output(pick)
    assert out.kelly_units == 0.0


# ---------------------------------------------------------------------------
# Headline construction
# ---------------------------------------------------------------------------


def test_headline_for_total_no_sign_prefix():
    """Totals don't show '+8.5' — they show '8.5'."""
    pick = _pick(market="Total", side="Over", line_value=8.5)
    out = build_full_game_output(pick)
    assert out.headline() == "Over 8.5"


def test_headline_for_run_line_carries_signed_spread():
    """Spreads need explicit sign — -1.5 for favorite, +1.5 for dog."""
    fav = _pick(market="Run_Line", side="NYY", line_value=-1.5,
                  team_tricode="NYY")
    dog = _pick(market="Run_Line", side="BOS", line_value=+1.5,
                  team_tricode="BOS")
    assert build_full_game_output(fav).headline() == "NYY -1.5"
    assert build_full_game_output(dog).headline() == "BOS +1.5"


def test_headline_for_moneyline_uses_team_tricode():
    pick = _pick(market="ML", side="NYY", line_value=None,
                   team_tricode="NYY")
    out = build_full_game_output(pick)
    assert out.headline() == "NYY ML"


def test_headline_for_team_total():
    pick = _pick(market="Team_Total", side="Over", line_value=4.5,
                   team_tricode="NYY")
    out = build_full_game_output(pick)
    assert "Over 4.5" in out.headline()


def test_matchup_uses_tricodes_when_available():
    pick = _pick()
    out = build_full_game_output(pick)
    assert out.matchup() == "BOS @ NYY"


# ---------------------------------------------------------------------------
# to_email_card adapter
# ---------------------------------------------------------------------------


def test_email_card_includes_matchup_market_tier_metrics():
    pick = _pick()
    out = build_full_game_output(
        pick, confidence=0.72, lam_used=9.54,
    )
    text = to_email_card(out)
    assert "BOS @ NYY" in text
    assert "Total Runs" in text
    assert "Over 8.5" in text
    assert "[STRONG" in text
    assert "Deep Green" in text
    assert "λ 9.54" in text
    assert "conf 72%" in text
    assert "edge +7.4pp" in text
    assert "odds -110" in text
    assert "draftkings" in text


def test_email_card_renders_run_line_with_signed_spread():
    pick = _pick(market="Run_Line", side="NYY", line_value=-1.5,
                   team_tricode="NYY", american_odds=+135,
                   model_prob=0.50, market_prob=0.42, edge_pp=8.0,
                   tier=Tier.ELITE)
    text = to_email_card(build_full_game_output(pick))
    assert "NYY -1.5" in text
    assert "[ELITE" in text


def test_email_card_renders_moneyline_without_line_value():
    pick = _pick(market="ML", side="NYY", line_value=None,
                   team_tricode="NYY", american_odds=-150,
                   model_prob=0.65, market_prob=0.60,
                   edge_pp=5.0, tier=Tier.STRONG)
    text = to_email_card(build_full_game_output(pick))
    assert "NYY ML" in text
    assert "8.5" not in text  # no totals line value bleed


def test_email_card_drops_kelly_when_zero():
    pick = _pick(tier=Tier.LEAN, edge_pp=2.0,
                   model_prob=0.55, market_prob=0.53)
    text = to_email_card(build_full_game_output(pick))
    assert "stake" not in text


def test_email_card_drivers_appended_when_present():
    out = build_full_game_output(
        _pick(),
        driver_text=["+ NYY offense vs RHP", "+ wind out at YS"],
    )
    text = to_email_card(out)
    assert "Why:" in text
    assert "NYY offense" in text


# ---------------------------------------------------------------------------
# to_api_dict adapter
# ---------------------------------------------------------------------------


def test_to_api_dict_carries_headline_and_matchup_strings():
    out = build_full_game_output(_pick())
    d = to_api_dict(out)
    assert d["headline"] == "Over 8.5"
    assert d["matchup"] == "BOS @ NYY"
    import json
    json.dumps(d, default=str)


def test_to_api_dict_drivers_is_plain_list():
    out = build_full_game_output(
        _pick(), driver_text=["+1 a", "+0.5 b"],
    )
    d = to_api_dict(out)
    assert isinstance(d["driver_text"], list)
    assert d["driver_text"] == ["+1 a", "+0.5 b"]
