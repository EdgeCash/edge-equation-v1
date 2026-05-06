"""Tests for the strict MLB parlay engines (game-results + player-props).

These engines wrap the shared ``engines.parlay`` builder with the
audit-locked "Facts. Not Feelings." policy:

* 3–6 legs only.
* Each leg ≥4pp edge against de-vigged closing line OR ELITE tier.
* Combined EV positive after vig.
* Confidence above the league-prior baseline.
* CLV not catastrophically negative.
* No qualified parlay → explicit explanation, never a forced ticket.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from edge_equation.engines.mlb.game_results_parlay import (
    EnrichedLeg,
    MLBGameResultsParlayEngine,
    build_game_results_legs,
    build_game_results_parlay,
    filter_legs_by_strict_rules,
)
from edge_equation.engines.mlb.player_props_parlay import (
    MLBPlayerPropsParlayEngine,
    build_player_props_parlay,
)
from edge_equation.engines.mlb.thresholds import (
    MLB_PARLAY_RULES,
    NO_QUALIFIED_PARLAY_MESSAGE,
    PARLAY_CARD_NOTE,
)
from edge_equation.engines.parlay import ParlayLeg
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Helpers — fake projection rows that match the duck-typed attrs the
# parlay engines read from FullGameOutput / PropOutput.
# ---------------------------------------------------------------------------


def _full_game_row(
    market_type="ML",
    side="Yankees",
    team_tricode="NYY",
    line_value=None,
    model_prob=0.62,
    edge_pp=5.0,
    american_odds=-110.0,
    confidence=0.55,
    tier="STRONG",
    event_id="g1",
    clv_pp=0.0,
):
    return SimpleNamespace(
        market_type=market_type, side=side, team_tricode=team_tricode,
        line_value=line_value, model_prob=model_prob, edge_pp=edge_pp,
        american_odds=american_odds, confidence=confidence, tier=tier,
        event_id=event_id, clv_pp=clv_pp,
    )


def _prop_row(
    market_type="HR",
    market_label="Home Runs",
    player_name="Aaron Judge",
    line_value=0.5,
    side="Over",
    model_prob=0.55,
    edge_pp=4.5,
    american_odds=+150.0,
    confidence=0.65,
    tier="STRONG",
    game_id="g1",
    clv_pp=0.0,
):
    return SimpleNamespace(
        market_type=market_type, market_label=market_label,
        player_name=player_name, line_value=line_value, side=side,
        model_prob=model_prob, edge_pp=edge_pp,
        american_odds=american_odds, confidence=confidence, tier=tier,
        game_id=game_id, player_id=player_name, clv_pp=clv_pp,
    )


# ---------------------------------------------------------------------------
# Threshold rule sanity
# ---------------------------------------------------------------------------


def test_thresholds_min_max_legs_match_audit():
    assert MLB_PARLAY_RULES.min_legs == 3
    assert MLB_PARLAY_RULES.max_legs == 6


def test_thresholds_card_note_mentions_3_to_6_legs():
    assert "3" in PARLAY_CARD_NOTE and "6" in PARLAY_CARD_NOTE


def test_no_qualified_message_is_audit_text():
    # Must include the exact audit phrasing so the website renders the
    # canonical line.
    assert "No qualified parlay today" in NO_QUALIFIED_PARLAY_MESSAGE


def test_leg_qualifies_requires_either_edge_or_elite():
    rules = MLB_PARLAY_RULES
    # Below 4pp edge AND not ELITE → fails.
    assert not rules.leg_qualifies(
        market_type="ML", edge_frac=0.03, tier=Tier.STRONG,
        confidence=0.55, market_universe="game_results",
    )
    # Below 4pp edge but ELITE → passes (LOCK bypass).
    assert rules.leg_qualifies(
        market_type="ML", edge_frac=0.01, tier=Tier.ELITE,
        confidence=0.55, market_universe="game_results",
    )
    # Above 4pp edge, STRONG → passes.
    assert rules.leg_qualifies(
        market_type="ML", edge_frac=0.05, tier=Tier.STRONG,
        confidence=0.55, market_universe="game_results",
    )


def test_leg_qualifies_rejects_low_confidence():
    rules = MLB_PARLAY_RULES
    assert not rules.leg_qualifies(
        market_type="ML", edge_frac=0.10, tier=Tier.ELITE,
        confidence=0.30,        # at the league-prior baseline
        market_universe="game_results",
    )


def test_leg_qualifies_rejects_market_outside_universe():
    rules = MLB_PARLAY_RULES
    # HR is a player-prop market — should be rejected from the
    # game-results universe even with a huge edge.
    assert not rules.leg_qualifies(
        market_type="HR", edge_frac=0.20, tier=Tier.ELITE,
        confidence=0.80, market_universe="game_results",
    )


# ---------------------------------------------------------------------------
# Leg adapter / filter tests
# ---------------------------------------------------------------------------


def test_build_game_results_legs_pulls_full_game_outputs():
    rows = [_full_game_row() for _ in range(3)]
    legs = build_game_results_legs(full_game_outputs=rows)
    assert len(legs) == 3
    assert all(isinstance(l, EnrichedLeg) for l in legs)
    assert all(isinstance(l.leg, ParlayLeg) for l in legs)


def test_filter_drops_below_threshold_legs():
    qualifying_row = _full_game_row(
        market_type="ML", model_prob=0.62, edge_pp=5.0,
        confidence=0.55, tier="STRONG", event_id="g1",
    )
    weak_row = _full_game_row(
        market_type="Total", side="Over", line_value=8.5,
        model_prob=0.52, edge_pp=1.5, confidence=0.55, tier="LEAN",
        event_id="g2",
    )
    legs = build_game_results_legs(
        full_game_outputs=[qualifying_row, weak_row],
    )
    filtered = filter_legs_by_strict_rules(legs)
    assert len(filtered) == 1
    assert filtered[0].leg.market_type == "ML"


def test_filter_keeps_elite_below_4pp_edge():
    """ELITE tier bypasses the 4pp edge threshold (Signal Elite / LOCK)."""
    elite_row = _full_game_row(
        market_type="Run_Line", side="Yankees", team_tricode="NYY",
        line_value=-1.5, model_prob=0.65, edge_pp=2.0,   # tiny edge
        confidence=0.70, tier="ELITE", event_id="g1",
    )
    legs = build_game_results_legs(full_game_outputs=[elite_row])
    filtered = filter_legs_by_strict_rules(legs)
    assert len(filtered) == 1


# ---------------------------------------------------------------------------
# Game-results parlay end-to-end tests
# ---------------------------------------------------------------------------


def test_no_qualifying_combinations_emits_audit_message():
    """No qualifying legs → engine emits the audit's no-parlay message,
    no candidates."""
    weak = _full_game_row(
        edge_pp=1.0, tier="LEAN", confidence=0.55,
    )
    card = build_game_results_parlay(full_game_outputs=[weak])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_only_two_qualifying_legs_does_not_build_parlay():
    """Audit floor is 3 legs — a 2-leg pool yields no candidates."""
    qualifying = [
        _full_game_row(market_type="ML", event_id=f"g{i}",
                          model_prob=0.62, edge_pp=5.0,
                          confidence=0.55, tier="STRONG")
        for i in range(2)
    ]
    card = build_game_results_parlay(full_game_outputs=qualifying)
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_three_strong_legs_can_build_a_qualifying_parlay():
    """Three strong cross-game ML picks at +110 with 60% conviction
    each have:
      - independent joint  ≈ 0.216
      - corr-adjusted (independent)  ≈ 0.216
      - combined decimal  ≈ 9.26x
      - implied 0.108
      - EV at 0.5u  ≈ 0.5 * (0.216 * 8.26 - 0.784)  ≈ 0.50u

    That clears every strict gate; the engine must emit at least one
    candidate ticket.
    """
    rows = [
        _full_game_row(
            market_type="ML", side=f"Team{i}", team_tricode=f"T{i}",
            line_value=None, model_prob=0.60, edge_pp=5.0,
            american_odds=+110.0, confidence=0.55, tier="STRONG",
            event_id=f"g{i}",
        )
        for i in range(3)
    ]
    card = build_game_results_parlay(full_game_outputs=rows)
    assert card.has_qualified, card.explanation
    assert all(c.n_legs >= 3 for c in card.candidates)
    assert all(c.n_legs <= 6 for c in card.candidates)


def test_engine_rejects_seven_leg_combinations():
    """The strict rules cap at 6 legs — even with 7 strong cross-game
    picks the engine won't emit anything > 6 legs."""
    rows = [
        _full_game_row(
            market_type="ML", side=f"Team{i}", team_tricode=f"T{i}",
            line_value=None, model_prob=0.60, edge_pp=5.0,
            american_odds=+110.0, confidence=0.55, tier="STRONG",
            event_id=f"g{i}",
        )
        for i in range(7)
    ]
    card = build_game_results_parlay(full_game_outputs=rows)
    assert card.has_qualified
    for cand in card.candidates:
        assert cand.n_legs <= 6


def test_engine_class_run_method_never_raises_on_empty_input():
    engine = MLBGameResultsParlayEngine()
    card = engine.run(full_game_outputs=[], nrfi_rows=[])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation
    # Card still carries the audit's "3–6 legs only — built from proven
    # edges only." note so the website can render it.
    assert "3" in card.note and "6" in card.note


# ---------------------------------------------------------------------------
# Player-props parlay end-to-end tests
# ---------------------------------------------------------------------------


def test_player_props_parlay_three_qualifying_legs():
    rows = [
        _prop_row(
            market_type=mkt, model_prob=0.60, edge_pp=5.0,
            american_odds=+115.0, confidence=0.65, tier="STRONG",
            game_id=f"g{i}", player_name=f"P{i}",
        )
        for i, mkt in enumerate(("Hits", "RBI", "Total_Bases"))
    ]
    card = build_player_props_parlay(prop_outputs=rows)
    assert card.has_qualified, card.explanation
    assert all(3 <= c.n_legs <= 6 for c in card.candidates)


def test_player_props_parlay_rejects_unsupported_market():
    """A market not in the allowed player-prop universe is dropped."""
    rows = [
        _prop_row(market_type="Walks", model_prob=0.60, edge_pp=5.0,
                    confidence=0.65, tier="STRONG", player_name=f"P{i}",
                    game_id=f"g{i}")
        for i in range(3)
    ]
    card = build_player_props_parlay(prop_outputs=rows)
    assert not card.has_qualified
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


def test_engine_class_run_method_for_props_handles_empty_input():
    engine = MLBPlayerPropsParlayEngine()
    card = engine.run(prop_outputs=[])
    assert card.candidates == []
    assert NO_QUALIFIED_PARLAY_MESSAGE in card.explanation


# ---------------------------------------------------------------------------
# Engine registry wiring
# ---------------------------------------------------------------------------


def test_engine_registry_exposes_two_new_parlay_keys():
    from edge_equation.engine.engine_registry import list_engines
    keys = list_engines()
    assert "mlb_game_results_parlay" in keys
    assert "mlb_player_props_parlay" in keys
    assert "mlb_daily" in keys


# ---------------------------------------------------------------------------
# Final polish — transparency note, CLV logger, footer, status flags
# ---------------------------------------------------------------------------


def test_parlay_card_carries_transparency_note():
    """Every parlay card payload exposes the audit-locked transparency
    sentence so the website + daily card render the same string."""
    card = MLBGameResultsParlayEngine().run(
        full_game_outputs=[], nrfi_rows=[],
    )
    assert "Facts. Not Feelings." in card.transparency_note
    assert "≥4pp" in card.transparency_note or "4pp" in card.transparency_note

    props_card = MLBPlayerPropsParlayEngine().run(prop_outputs=[])
    assert "Facts. Not Feelings." in props_card.transparency_note


def test_render_card_block_includes_transparency_note():
    """Plain-text renderer surfaces the transparency note in the
    header AND in the no-qualified branch."""
    from edge_equation.engines.mlb.game_results_parlay import (
        render_card_block as render_game,
    )
    text = render_game([], header="GAME-RESULTS PARLAY")
    assert "Facts. Not Feelings." in text


def test_log_parlay_clv_snapshot_safe_on_empty_input():
    """The CLV snapshot helper is best-effort — an empty candidate
    list returns 0 without raising."""
    from edge_equation.engines.mlb.game_results_parlay import (
        log_parlay_clv_snapshot,
    )
    n = log_parlay_clv_snapshot(
        candidates=[], universe="game_results", target_date="2026-05-06",
    )
    assert n == 0


def test_thresholds_export_transparency_note():
    from edge_equation.engines.mlb import (
        PARLAY_TRANSPARENCY_NOTE, NO_QUALIFIED_PARLAY_MESSAGE,
    )
    assert "Facts. Not Feelings." in PARLAY_TRANSPARENCY_NOTE
    assert "No qualified parlay today" in NO_QUALIFIED_PARLAY_MESSAGE


def test_feed_bundle_carries_footer_and_market_status():
    """The website FeedBundle JSON exposes the freshness footer +
    per-market availability flags so the daily-edge page can render
    'Updated …' and 'Pending' / 'Limited Data' badges."""
    from datetime import datetime, timezone
    from edge_equation.engines.website.build_daily_feed import (
        FeedBundle,
    )
    bundle = FeedBundle(
        date="2026-05-06",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        market_status={"nrfi": "OK", "fullgame": "Limited Data"},
    )
    out = bundle.to_dict()
    assert "footer" in out and "Updated:" in out["footer"]
    assert out["market_status"]["fullgame"] == "Limited Data"
    assert out["parlays"]["transparency_note"]
    assert "Facts. Not Feelings." in out["parlays"]["transparency_note"]
