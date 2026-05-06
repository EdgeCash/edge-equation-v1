"""Tests for the strict WNBA parlay engines (game-results + player-props).

Mirrors `tests/test_mlb_parlay_engines.py` for the WNBA universe.
Same audit-locked policy applies: 3–6 legs, ≥4pp edge OR ELITE,
EV>0 after vig, no forced parlays.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from edge_equation.engines.wnba.game_results_parlay import (
    EnrichedLeg,
    WNBAGameResultsParlayEngine,
    build_game_results_legs,
    build_game_results_parlay,
    filter_legs_by_strict_rules,
)
from edge_equation.engines.wnba.player_props_parlay import (
    WNBAPlayerPropsParlayEngine,
    build_player_props_parlay,
)
from edge_equation.engines.wnba.thresholds import (
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
    PARLAY_TRANSPARENCY_NOTE,
    WNBA_PARLAY_RULES,
)
from edge_equation.engines.parlay import ParlayLeg
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Helpers — fake WNBA Output rows
# ---------------------------------------------------------------------------


def _game_row(
    market_type="fullgame_ml",
    side="Home",
    team="LAS",
    opponent="PHX",
    line=0.0,
    probability=0.62,
    edge_pp=5.0,
    confidence=0.55,
    tier="STRONG",
    game_id="g1",
    clv_pp=0.0,
):
    return SimpleNamespace(
        market=market_type, side=side, team=team, opponent=opponent,
        line=line, probability=probability, edge_pp=edge_pp,
        confidence=confidence, tier=tier, game_id=game_id, clv_pp=clv_pp,
        american_odds=-110.0, edge=edge_pp / 100.0 * line,
    )


def _prop_row(
    market_type="points",
    player="A'ja Wilson",
    team="LAS",
    line=22.5,
    side="Over",
    probability=0.60,
    edge_pp=5.0,
    confidence=0.65,
    tier="STRONG",
    game_id="g1",
    clv_pp=0.0,
):
    return SimpleNamespace(
        market=market_type, player=player, team=team,
        line=line, side=side, probability=probability,
        edge_pp=edge_pp, confidence=confidence, tier=tier,
        game_id=game_id, player_id=player, clv_pp=clv_pp,
        american_odds=+110.0,
    )


# ---------------------------------------------------------------------------
# Threshold rule sanity
# ---------------------------------------------------------------------------


def test_wnba_thresholds_min_max_legs_match_audit():
    assert WNBA_PARLAY_RULES.min_legs == 3
    assert WNBA_PARLAY_RULES.max_legs == 6


def test_wnba_thresholds_card_note_mentions_3_to_6_legs():
    assert "3" in PARLAY_CARD_NOTE and "6" in PARLAY_CARD_NOTE


def test_wnba_no_qualified_message_is_audit_text():
    assert "No qualified parlay today" in NO_QUALIFIED_PARLAY_MESSAGE


def test_wnba_transparency_note_includes_facts_not_feelings():
    assert "Facts. Not Feelings." in PARLAY_TRANSPARENCY_NOTE


def test_wnba_leg_qualifies_requires_either_edge_or_elite():
    rules = WNBA_PARLAY_RULES
    assert not rules.leg_qualifies(
        market_type="fullgame_ml", edge_frac=0.03, tier=Tier.STRONG,
        confidence=0.55, market_universe="game_results",
    )
    assert rules.leg_qualifies(
        market_type="fullgame_ml", edge_frac=0.01, tier=Tier.ELITE,
        confidence=0.55, market_universe="game_results",
    )
    assert rules.leg_qualifies(
        market_type="fullgame_ml", edge_frac=0.05, tier=Tier.STRONG,
        confidence=0.55, market_universe="game_results",
    )


def test_wnba_leg_qualifies_rejects_non_wnba_market():
    rules = WNBA_PARLAY_RULES
    # MLB-only market → rejected from WNBA universes.
    assert not rules.leg_qualifies(
        market_type="NRFI", edge_frac=0.20, tier=Tier.ELITE,
        confidence=0.80, market_universe="game_results",
    )


# ---------------------------------------------------------------------------
# Leg adapter tests
# ---------------------------------------------------------------------------


def test_build_game_results_legs_pulls_wnba_outputs():
    rows = [_game_row(team=f"T{i}", opponent=f"O{i}", game_id=f"g{i}")
            for i in range(3)]
    legs = build_game_results_legs(wnba_outputs=rows)
    assert len(legs) == 3
    assert all(isinstance(l, EnrichedLeg) for l in legs)
    assert all(isinstance(l.leg, ParlayLeg) for l in legs)


def test_filter_drops_below_threshold_wnba_legs():
    qualifying = _game_row(
        market_type="fullgame_ml", probability=0.62, edge_pp=5.0,
        confidence=0.55, tier="STRONG", game_id="g1",
    )
    weak = _game_row(
        market_type="fullgame_total", side="Over", line=160.5,
        probability=0.52, edge_pp=1.5, confidence=0.55, tier="LEAN",
        game_id="g2",
    )
    legs = build_game_results_legs(wnba_outputs=[qualifying, weak])
    filtered = filter_legs_by_strict_rules(legs)
    assert len(filtered) == 1
    assert filtered[0].leg.market_type == "fullgame_ml"


# ---------------------------------------------------------------------------
# End-to-end engine tests
# ---------------------------------------------------------------------------


def test_no_qualifying_combinations_emits_audit_message():
    weak = _game_row(edge_pp=1.0, tier="LEAN", confidence=0.55)
    card = build_game_results_parlay(wnba_outputs=[weak])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_only_two_qualifying_legs_does_not_build_parlay():
    rows = [
        _game_row(market_type="fullgame_ml", game_id=f"g{i}",
                    probability=0.62, edge_pp=5.0,
                    confidence=0.55, tier="STRONG", team=f"T{i}",
                    opponent=f"O{i}")
        for i in range(2)
    ]
    card = build_game_results_parlay(wnba_outputs=rows)
    assert card.candidates == []


def test_three_strong_wnba_legs_can_build_a_qualifying_parlay():
    rows = [
        _game_row(
            market_type="fullgame_ml", side=f"T{i}", team=f"T{i}",
            opponent=f"O{i}", line=0.0, probability=0.60, edge_pp=5.0,
            confidence=0.55, tier="STRONG", game_id=f"g{i}",
        )
        for i in range(3)
    ]
    # Patch each row to use +110 American odds so the math comes out
    # to a positive-EV 3-leg combo.
    for r in rows:
        r.american_odds = +110.0
    card = build_game_results_parlay(wnba_outputs=rows)
    assert card.has_qualified, card.explanation
    assert all(3 <= c.n_legs <= 6 for c in card.candidates)


def test_engine_class_run_method_never_raises_on_empty_input():
    engine = WNBAGameResultsParlayEngine()
    card = engine.run(wnba_outputs=[])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation
    # Card carries the audit-locked transparency note.
    assert "Facts. Not Feelings." in card.transparency_note


def test_player_props_parlay_three_qualifying_legs():
    rows = [
        _prop_row(
            market_type=mkt, probability=0.60, edge_pp=5.0,
            confidence=0.65, tier="STRONG",
            game_id=f"g{i}", player=f"P{i}",
        )
        for i, mkt in enumerate(("points", "rebounds", "assists"))
    ]
    card = build_player_props_parlay(wnba_prop_outputs=rows)
    assert card.has_qualified, card.explanation
    assert all(3 <= c.n_legs <= 6 for c in card.candidates)


def test_player_props_parlay_rejects_unsupported_market():
    rows = [
        _prop_row(market_type="dunks", probability=0.60, edge_pp=5.0,
                    confidence=0.65, tier="STRONG", player=f"P{i}",
                    game_id=f"g{i}")
        for i in range(3)
    ]
    card = build_player_props_parlay(wnba_prop_outputs=rows)
    assert not card.has_qualified
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_engine_class_run_method_for_props_handles_empty_input():
    engine = WNBAPlayerPropsParlayEngine()
    card = engine.run(wnba_prop_outputs=[])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


# ---------------------------------------------------------------------------
# Engine registry feature flag
# ---------------------------------------------------------------------------


def test_engine_registry_lists_wnba_keys():
    from edge_equation.engine.engine_registry import list_engines
    keys = list_engines()
    assert "wnba_game_results_parlay" in keys
    assert "wnba_player_props_parlay" in keys
    assert "wnba_daily" in keys
    assert "wnba" in keys


def test_engine_registry_feature_flag_default_off(monkeypatch):
    """Without EDGE_FEATURE_WNBA_PARLAYS, parlay keys return None."""
    monkeypatch.delenv("EDGE_FEATURE_WNBA_PARLAYS", raising=False)
    from edge_equation.engine.engine_registry import get_engine
    assert get_engine("wnba_game_results_parlay") is None
    assert get_engine("wnba_player_props_parlay") is None
    assert get_engine("wnba_daily") is None
    # Per-row engine is always live regardless of the flag.
    assert get_engine("wnba") is not None


def test_engine_registry_feature_flag_on(monkeypatch):
    monkeypatch.setenv("EDGE_FEATURE_WNBA_PARLAYS", "on")
    from edge_equation.engine.engine_registry import get_engine
    e1 = get_engine("wnba_game_results_parlay")
    e2 = get_engine("wnba_player_props_parlay")
    runner = get_engine("wnba_daily")
    assert e1 is not None
    assert e2 is not None
    assert runner is not None


# ---------------------------------------------------------------------------
# Website feed extension
# ---------------------------------------------------------------------------


def test_feed_bundle_carries_wnba_section():
    """The website FeedBundle JSON exposes the wnba key with parlay
    transparency note + empty default sections."""
    from datetime import datetime, timezone
    from edge_equation.engines.website.build_daily_feed import FeedBundle
    bundle = FeedBundle(
        date="2026-05-09",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        market_status={"wnba": "Pending"},
    )
    out = bundle.to_dict()
    assert "wnba" in out
    assert "parlays" in out["wnba"]
    assert "Facts. Not Feelings." in out["wnba"]["parlays"]["transparency_note"]
    assert out["wnba"]["parlays"]["game_results"] == []
    assert out["wnba"]["parlays"]["player_props"] == []
    assert out["market_status"]["wnba"] == "Pending"
