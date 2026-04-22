"""Phase 20 posting formatter: new card types + Daily Edge top-5 A/A+
filter + Overseas filter + Evening Edge stability + ledger footer."""
from decimal import Decimal
import pytest

from edge_equation.compliance import (
    DISCLAIMER_TEXT,
    LEDGER_FOOTER_RE,
    compliance_test,
)
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.ledger import LedgerStats
from edge_equation.posting.posting_formatter import (
    DAILY_EDGE_TOP_N,
    MULTI_LEG_MAX,
    MULTI_LEG_MIN,
    PostingFormatter,
)


def _pick(sport="MLB", market_type="ML", grade="A", selection="BOS",
          edge=Decimal('0.06'), odds=-110, game_id=None):
    return Pick(
        sport=sport, market_type=market_type, selection=selection,
        line=Line(odds=odds),
        fair_prob=Decimal('0.55'),
        edge=edge, kelly=Decimal('0.01'),
        grade=grade, realization=68,
        game_id=game_id or f"G-{market_type}-{selection}",
        metadata={"home_team": "NYY", "away_team": "LAA"},
    )


# ------------------------------------------------ Daily Edge filter


def test_daily_edge_keeps_only_a_plus_and_a():
    picks = [
        _pick(grade="A+", game_id="G1"),
        _pick(grade="A", game_id="G2"),
        _pick(grade="B", game_id="G3"),
        _pick(grade="C", game_id="G4"),
    ]
    out = PostingFormatter.filter_daily_edge(picks)
    grades = [p.grade for p in out]
    assert grades == ["A+", "A"]


def test_daily_edge_caps_at_five():
    picks = [_pick(grade="A+", game_id=f"G{i}") for i in range(10)]
    out = PostingFormatter.filter_daily_edge(picks)
    assert len(out) == DAILY_EDGE_TOP_N


def test_daily_edge_sorts_a_plus_before_a():
    picks = [
        _pick(grade="A", selection="A1", edge=Decimal('0.09')),
        _pick(grade="A+", selection="AP1", edge=Decimal('0.08')),
    ]
    out = PostingFormatter.filter_daily_edge(picks)
    # A+ comes first even though the A has higher edge -- grade tier dominates
    assert out[0].grade == "A+"


def test_daily_edge_sorts_by_edge_within_tier():
    picks = [
        _pick(grade="A+", selection="X", edge=Decimal('0.08')),
        _pick(grade="A+", selection="Y", edge=Decimal('0.12')),
    ]
    out = PostingFormatter.filter_daily_edge(picks)
    assert out[0].selection == "Y"


def test_daily_edge_no_forcing_when_less_than_5_qualify():
    picks = [_pick(grade="A", game_id="G1"), _pick(grade="B", game_id="G2")]
    card = PostingFormatter.build_card(
        "daily_edge", picks, generated_at="2026-04-22T11:00",
    )
    # Only 1 pick qualifies; card must post it, nothing forced.
    assert len(card["picks"]) == 1
    assert card["picks"][0]["grade"] == "A"


# ------------------------------------------------ Overseas Edge filter


def test_overseas_excludes_domestic_sports():
    picks = [
        _pick(sport="MLB", game_id="mlb1"),
        _pick(sport="KBO", game_id="kbo1"),
        _pick(sport="NPB", game_id="npb1"),
        _pick(sport="Soccer", game_id="soc1"),
        _pick(sport="NHL", game_id="nhl1"),
    ]
    out = PostingFormatter.filter_overseas(picks)
    sports = {p.sport for p in out}
    assert sports == {"KBO", "NPB", "Soccer"}


def test_overseas_excludes_all_props():
    picks = [
        _pick(sport="KBO", market_type="ML", selection="LG"),
        _pick(sport="KBO", market_type="Total", selection="Over 9.5"),
        _pick(sport="KBO", market_type="HR", selection="Ramos over 0.5"),
        _pick(sport="NPB", market_type="K", selection="Yamamoto over 7.5"),
    ]
    out = PostingFormatter.filter_overseas(picks)
    types = {p.market_type for p in out}
    assert "HR" not in types
    assert "K" not in types
    assert "ML" in types
    assert "Total" in types


# ------------------------------------------------ Evening Edge stability


def test_evening_edge_stable_short_form():
    picks = [_pick(game_id="G1"), _pick(game_id="G2")]
    # Prior matches current exactly -> stable
    card = PostingFormatter.build_card(
        "evening_edge", picks, prior_picks=picks,
    )
    assert card["picks"] == []
    assert "engine stable" in card["subhead"].lower()


def test_evening_edge_non_stable_passes_picks_through():
    current = [_pick(game_id="G1"), _pick(game_id="G2")]
    prior = [_pick(game_id="G1"), _pick(game_id="G3")]
    card = PostingFormatter.build_card(
        "evening_edge", current, prior_picks=prior,
    )
    assert len(card["picks"]) == 2


def test_evening_edge_no_prior_posts_full():
    picks = [_pick(game_id="G1")]
    card = PostingFormatter.build_card("evening_edge", picks)
    assert len(card["picks"]) == 1


# ------------------------------------------------ Multi-Leg Projection


def test_multi_leg_valid_range_accepted():
    for n in range(MULTI_LEG_MIN, MULTI_LEG_MAX + 1):
        picks = [_pick(game_id=f"G{i}") for i in range(n)]
        card = PostingFormatter.build_card("multi_leg_projection", picks)
        assert len(card["picks"]) == n


def test_multi_leg_too_few_rejected():
    with pytest.raises(ValueError, match="multi_leg_projection"):
        PostingFormatter.build_card(
            "multi_leg_projection",
            [_pick(game_id="G1")] * (MULTI_LEG_MIN - 1),
        )


def test_multi_leg_too_many_rejected():
    with pytest.raises(ValueError, match="multi_leg_projection"):
        PostingFormatter.build_card(
            "multi_leg_projection",
            [_pick(game_id=f"G{i}") for i in range(MULTI_LEG_MAX + 1)],
        )


# ------------------------------------------------ The Ledger / Spotlight


def test_the_ledger_card_builds():
    card = PostingFormatter.build_card("the_ledger", [])
    assert card["card_type"] == "the_ledger"
    assert "The Ledger" == card["headline"]


def test_spotlight_card_builds():
    picks = [_pick(grade="A", game_id="GSpot")]
    card = PostingFormatter.build_card("spotlight", picks)
    assert card["card_type"] == "spotlight"
    assert "Spotlight" == card["headline"]


# ------------------------------------------------ public_mode + ledger footer


def test_public_mode_sanitizes_edge_kelly():
    picks = [_pick(grade="A+")]
    card = PostingFormatter.build_card(
        "daily_edge", picks, public_mode=True,
    )
    for p in card["picks"]:
        assert "edge" not in p
        assert "kelly" not in p


def test_public_mode_injects_disclaimer():
    picks = [_pick(grade="A+")]
    card = PostingFormatter.build_card(
        "daily_edge", picks, public_mode=True,
    )
    assert DISCLAIMER_TEXT in card["tagline"]


def test_public_mode_with_ledger_stats_injects_footer():
    picks = [_pick(grade="A+")]
    stats = LedgerStats(
        wins=68, losses=49, pushes=3,
        units_net=Decimal('8.45'), roi_pct=Decimal('7.0'),
        total_plays=120,
    )
    card = PostingFormatter.build_card(
        "daily_edge", picks, public_mode=True, ledger_stats=stats,
    )
    assert LEDGER_FOOTER_RE.search(card["tagline"]) is not None


def test_public_mode_ledger_footer_idempotent():
    picks = [_pick(grade="A+")]
    stats = LedgerStats(
        wins=1, losses=0, pushes=0,
        units_net=Decimal('0.91'), roi_pct=Decimal('91.0'),
        total_plays=1,
    )
    card = PostingFormatter.build_card(
        "daily_edge", picks, public_mode=True, ledger_stats=stats,
    )
    footer_count = card["tagline"].count("Season Ledger:")
    assert footer_count == 1


def test_public_mode_full_card_passes_compliance():
    picks = [_pick(grade="A+", game_id="G1"), _pick(grade="A", game_id="G2")]
    stats = LedgerStats(
        wins=12, losses=8, pushes=1,
        units_net=Decimal('3.20'), roi_pct=Decimal('15.2'),
        total_plays=21,
    )
    card = PostingFormatter.build_card(
        "daily_edge", picks, public_mode=True, ledger_stats=stats,
    )
    report = compliance_test(card, require_ledger_footer=True)
    assert report.ok is True, report.violations


def test_non_public_mode_skips_sanitizer_and_footer():
    picks = [_pick(grade="A+")]
    stats = LedgerStats(
        wins=1, losses=0, pushes=0,
        units_net=Decimal('0.91'), roi_pct=Decimal('91.0'),
        total_plays=1,
    )
    card = PostingFormatter.build_card(
        "daily_edge", picks, public_mode=False, ledger_stats=stats,
    )
    # Non-public mode preserves edge on picks (legacy behavior)
    assert "edge" in card["picks"][0]
    # No forced footer in non-public mode
    assert "Season Ledger:" not in card.get("tagline", "")
