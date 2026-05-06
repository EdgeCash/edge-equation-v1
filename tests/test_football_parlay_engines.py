"""Tests for the strict NFL + NCAAF parlay engines.

Mirrors `tests/test_wnba_parlay_engines.py` and
`tests/test_mlb_parlay_engines.py`. Same audit-locked policy: 3–6
legs, ≥4pp edge OR ELITE tier, EV>0 after vig, no forced parlays.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from edge_equation.engines.nfl.game_results_parlay import (
    NFLGameResultsParlayEngine,
    build_game_results_legs as nfl_build_legs,
    build_game_results_parlay as nfl_build_card,
    filter_legs_by_strict_rules as nfl_filter_legs,
)
from edge_equation.engines.nfl.player_props_parlay import (
    NFLPlayerPropsParlayEngine,
    build_player_props_parlay as nfl_build_props_card,
)
from edge_equation.engines.nfl.thresholds import (
    NFL_PARLAY_RULES,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
)
from edge_equation.engines.ncaaf.game_results_parlay import (
    NCAAFGameResultsParlayEngine,
    build_game_results_parlay as ncaaf_build_card,
)
from edge_equation.engines.ncaaf.player_props_parlay import (
    NCAAFPlayerPropsParlayEngine,
    build_player_props_parlay as ncaaf_build_props_card,
)
from edge_equation.engines.ncaaf.thresholds import NCAAF_PARLAY_RULES
from edge_equation.engines.parlay import ParlayLeg
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Helpers — fake football Output rows.
# ---------------------------------------------------------------------------


def _game_row(
    market_type="ML",
    side="home",
    home_tricode="KC",
    away_tricode="BUF",
    line_value=None,
    model_prob=0.62,
    edge_pp=5.0,
    confidence=0.55,
    tier="STRONG",
    event_id="g1",
    clv_pp=0.0,
):
    return SimpleNamespace(
        market_type=market_type, side=side,
        home_tricode=home_tricode, away_tricode=away_tricode,
        line_value=line_value, model_prob=model_prob, edge_pp=edge_pp,
        confidence=confidence, tier=tier, event_id=event_id,
        clv_pp=clv_pp, american_odds=-110.0,
    )


def _prop_row(
    market_type="Pass_Yds",
    market_label="Passing Yards",
    player_name="Patrick Mahomes",
    side="Over",
    line_value=275.5,
    model_prob=0.60,
    edge_pp=5.0,
    confidence=0.65,
    tier="STRONG",
    event_id="g1",
    clv_pp=0.0,
):
    return SimpleNamespace(
        market_type=market_type, market_label=market_label,
        player_name=player_name, side=side, line_value=line_value,
        model_prob=model_prob, edge_pp=edge_pp,
        confidence=confidence, tier=tier, event_id=event_id,
        clv_pp=clv_pp, american_odds=-110.0,
    )


# ---------------------------------------------------------------------------
# Threshold rule sanity (NFL + NCAAF share numerics — sample both).
# ---------------------------------------------------------------------------


def test_nfl_thresholds_min_max_legs_match_audit():
    assert NFL_PARLAY_RULES.min_legs == 3
    assert NFL_PARLAY_RULES.max_legs == 6


def test_ncaaf_thresholds_min_max_legs_match_audit():
    assert NCAAF_PARLAY_RULES.min_legs == 3
    assert NCAAF_PARLAY_RULES.max_legs == 6


def test_card_note_mentions_3_to_6_legs():
    assert "3" in PARLAY_CARD_NOTE and "6" in PARLAY_CARD_NOTE


def test_transparency_note_includes_facts_not_feelings():
    assert "Facts. Not Feelings." in PARLAY_TRANSPARENCY_NOTE


def test_nfl_leg_qualifies_requires_either_edge_or_elite():
    rules = NFL_PARLAY_RULES
    assert not rules.leg_qualifies(
        market_type="ML", edge_frac=0.03, tier=Tier.STRONG,
        confidence=0.55, market_universe="game_results",
    )
    assert rules.leg_qualifies(
        market_type="ML", edge_frac=0.01, tier=Tier.ELITE,
        confidence=0.55, market_universe="game_results",
    )
    assert rules.leg_qualifies(
        market_type="Spread", edge_frac=0.05, tier=Tier.STRONG,
        confidence=0.55, market_universe="game_results",
    )


def test_nfl_leg_qualifies_rejects_non_football_market():
    rules = NFL_PARLAY_RULES
    # MLB-only market → rejected from NFL universe.
    assert not rules.leg_qualifies(
        market_type="NRFI", edge_frac=0.20, tier=Tier.ELITE,
        confidence=0.80, market_universe="game_results",
    )


def test_ncaaf_leg_qualifies_rejects_basketball_market():
    rules = NCAAF_PARLAY_RULES
    assert not rules.leg_qualifies(
        market_type="points", edge_frac=0.20, tier=Tier.ELITE,
        confidence=0.80, market_universe="player_props",
    )


# ---------------------------------------------------------------------------
# Leg adapter tests (NFL).
# ---------------------------------------------------------------------------


def test_nfl_build_legs_pulls_outputs():
    rows = [_game_row(home_tricode=f"H{i}", away_tricode=f"A{i}",
                       event_id=f"g{i}")
            for i in range(3)]
    legs = nfl_build_legs(nfl_outputs=rows)
    assert len(legs) == 3
    assert all(isinstance(l.leg, ParlayLeg) for l in legs)


def test_nfl_filter_drops_below_threshold_legs():
    qualifying = _game_row(
        market_type="Spread", line_value=-3.5, model_prob=0.62,
        edge_pp=5.0, confidence=0.55, tier="STRONG", event_id="g1",
    )
    weak = _game_row(
        market_type="Total", side="Over", line_value=47.5,
        model_prob=0.52, edge_pp=1.5, confidence=0.55, tier="LEAN",
        event_id="g2",
    )
    legs = nfl_build_legs(nfl_outputs=[qualifying, weak])
    filtered = nfl_filter_legs(legs)
    assert len(filtered) == 1
    assert filtered[0].leg.market_type == "Spread"


# ---------------------------------------------------------------------------
# End-to-end parlay tests (NFL + NCAAF — both wired identically).
# ---------------------------------------------------------------------------


def test_nfl_no_qualifying_combinations_emits_audit_message():
    weak = _game_row(edge_pp=1.0, tier="LEAN", confidence=0.55)
    card = nfl_build_card(nfl_outputs=[weak])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_nfl_three_strong_legs_can_build_a_qualifying_parlay():
    rows = [
        _game_row(
            market_type="Spread", side=f"H{i}", home_tricode=f"H{i}",
            away_tricode=f"A{i}", line_value=-3.5, model_prob=0.60,
            edge_pp=5.0, confidence=0.55, tier="STRONG",
            event_id=f"g{i}",
        )
        for i in range(3)
    ]
    for r in rows:
        r.american_odds = +110.0
    card = nfl_build_card(nfl_outputs=rows)
    assert card.has_qualified, card.explanation
    assert all(3 <= c.n_legs <= 6 for c in card.candidates)


def test_nfl_engine_class_run_method_handles_empty_input():
    engine = NFLGameResultsParlayEngine()
    card = engine.run(nfl_outputs=[])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation
    assert "Facts. Not Feelings." in card.transparency_note


def test_nfl_player_props_parlay_three_qualifying_legs():
    rows = [
        _prop_row(
            market_type=mkt, model_prob=0.60, edge_pp=5.0,
            confidence=0.65, tier="STRONG",
            event_id=f"g{i}", player_name=f"P{i}",
        )
        for i, mkt in enumerate(("Pass_Yds", "Rush_Yds", "Rec_Yds"))
    ]
    for r in rows:
        r.american_odds = +115.0
    card = nfl_build_props_card(nfl_prop_outputs=rows)
    assert card.has_qualified, card.explanation
    assert all(3 <= c.n_legs <= 6 for c in card.candidates)


def test_nfl_player_props_parlay_rejects_non_football_market():
    rows = [
        _prop_row(market_type="HR", model_prob=0.60, edge_pp=5.0,
                    confidence=0.65, tier="STRONG",
                    player_name=f"P{i}", event_id=f"g{i}")
        for i in range(3)
    ]
    card = nfl_build_props_card(nfl_prop_outputs=rows)
    assert not card.has_qualified
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_ncaaf_three_strong_legs_can_build_a_qualifying_parlay():
    rows = [
        _game_row(
            market_type="Spread", side=f"H{i}", home_tricode=f"H{i}",
            away_tricode=f"A{i}", line_value=-7.5, model_prob=0.60,
            edge_pp=5.0, confidence=0.55, tier="STRONG",
            event_id=f"g{i}",
        )
        for i in range(3)
    ]
    for r in rows:
        r.american_odds = +110.0
    card = ncaaf_build_card(ncaaf_outputs=rows)
    assert card.has_qualified, card.explanation


def test_ncaaf_engine_class_run_method_handles_empty_input():
    engine = NCAAFGameResultsParlayEngine()
    card = engine.run(ncaaf_outputs=[])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_ncaaf_player_props_parlay_three_qualifying_legs():
    rows = [
        _prop_row(
            market_type=mkt, model_prob=0.60, edge_pp=5.0,
            confidence=0.65, tier="STRONG",
            event_id=f"g{i}", player_name=f"P{i}",
        )
        for i, mkt in enumerate(("Pass_Yds", "Rush_Yds", "Rec_Yds"))
    ]
    for r in rows:
        r.american_odds = +115.0
    card = ncaaf_build_props_card(ncaaf_prop_outputs=rows)
    assert card.has_qualified, card.explanation


# ---------------------------------------------------------------------------
# Engine registry feature flags.
# ---------------------------------------------------------------------------


def test_engine_registry_lists_football_keys():
    from edge_equation.engine.engine_registry import list_engines
    keys = list_engines()
    assert "nfl_game_results_parlay" in keys
    assert "nfl_player_props_parlay" in keys
    assert "nfl_daily" in keys
    assert "ncaaf_game_results_parlay" in keys
    assert "ncaaf_player_props_parlay" in keys
    assert "ncaaf_daily" in keys


def test_engine_registry_nfl_feature_flag_default_off(monkeypatch):
    monkeypatch.delenv("EDGE_FEATURE_NFL_PARLAYS", raising=False)
    from edge_equation.engine.engine_registry import get_engine
    assert get_engine("nfl_game_results_parlay") is None
    assert get_engine("nfl_player_props_parlay") is None
    assert get_engine("nfl_daily") is None


def test_engine_registry_nfl_feature_flag_on(monkeypatch):
    monkeypatch.setenv("EDGE_FEATURE_NFL_PARLAYS", "on")
    from edge_equation.engine.engine_registry import get_engine
    assert get_engine("nfl_game_results_parlay") is not None
    assert get_engine("nfl_player_props_parlay") is not None
    assert get_engine("nfl_daily") is not None


def test_engine_registry_ncaaf_feature_flag_default_off(monkeypatch):
    monkeypatch.delenv("EDGE_FEATURE_NCAAF_PARLAYS", raising=False)
    from edge_equation.engine.engine_registry import get_engine
    assert get_engine("ncaaf_game_results_parlay") is None
    assert get_engine("ncaaf_player_props_parlay") is None
    assert get_engine("ncaaf_daily") is None


def test_engine_registry_ncaaf_feature_flag_on(monkeypatch):
    monkeypatch.setenv("EDGE_FEATURE_NCAAF_PARLAYS", "on")
    from edge_equation.engine.engine_registry import get_engine
    assert get_engine("ncaaf_game_results_parlay") is not None
    assert get_engine("ncaaf_player_props_parlay") is not None
    assert get_engine("ncaaf_daily") is not None


# ---------------------------------------------------------------------------
# Website feed extension.
# ---------------------------------------------------------------------------


def test_feed_bundle_carries_nfl_and_ncaaf_sections():
    from datetime import datetime, timezone
    from edge_equation.engines.website.build_daily_feed import FeedBundle
    bundle = FeedBundle(
        date="2026-09-04",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        market_status={"nfl": "Pending", "ncaaf": "Pending"},
    )
    out = bundle.to_dict()
    assert "nfl" in out and "ncaaf" in out
    assert out["nfl"]["parlays"]["game_results"] == []
    assert out["ncaaf"]["parlays"]["game_results"] == []
    assert "Facts. Not Feelings." in out["nfl"]["parlays"]["transparency_note"]
    assert "Facts. Not Feelings." in out["ncaaf"]["parlays"]["transparency_note"]
    assert out["market_status"]["nfl"] == "Pending"
    assert out["market_status"]["ncaaf"] == "Pending"
