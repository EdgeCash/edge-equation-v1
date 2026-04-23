"""
Player Prop Projections section.

Free-content brand rules:
  - Exact pipe-separated format, no Top-N language, no DFS/app mentions.
  - Admits A+ / A grade props only. No forcing content when empty.
  - No unit / edge / Kelly leakage in the rendered section.
  - Integrates into daily_edge + spotlight cards; doesn't appear on
    ledger / evening_edge / overseas_edge.
  - Single-footer invariant: disclaimer + Season Ledger footer still
    appear exactly once at the bottom of the X post.
"""
from decimal import Decimal

import pytest

from edge_equation.compliance import compliance_test
from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.ledger import LedgerStats
from edge_equation.posting.player_props import (
    PROP_MARKET_LABEL,
    PROP_MARKETS,
    PropProjectionRow,
    _player_name,
    build_prop_rows,
    render_prop_section,
    select_prop_projections,
)
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.publishing.x_formatter import format_card


def _prop(
    market="HR", grade="A+", expected="0.82",
    player="Aaron Judge", read="Barrel rate +5pp last 2 weeks",
    game_id="G1", away="LAA", home="NYY",
):
    return Pick(
        sport="MLB", market_type=market,
        selection=f"{player} over 0.5",
        line=Line(odds=+280),
        fair_prob=Decimal("0.55"),
        expected_value=Decimal(expected) if expected is not None else None,
        edge=Decimal("0.09"),
        kelly=Decimal("0.025"),
        grade=grade,
        game_id=game_id,
        metadata={
            "home_team": home, "away_team": away,
            "player_name": player, "read_notes": read,
        },
    )


def _team_pick(grade="A", edge="0.06"):
    return Pick(
        sport="MLB", market_type="ML", selection="NYY",
        line=Line(odds=-115),
        fair_prob=Decimal("0.55"),
        edge=Decimal(edge), kelly=Decimal("0.03"),
        grade=grade, game_id="G1",
        metadata={"home_team": "NYY", "away_team": "BOS"},
    )


def _ledger_zero():
    return LedgerStats(
        wins=0, losses=0, pushes=0,
        units_net=Decimal("0"), roi_pct=Decimal("0.0"), total_plays=0,
    )


# ------------------------------------------------ selection


def test_select_admits_aplus_and_a_prop_picks():
    picks = [
        _prop(grade="A+"),
        _prop(grade="A", market="K", player="Gerrit Cole", expected="7.4"),
        _prop(grade="B", market="HR", player="Mike Trout"),
        _team_pick(grade="A"),
        _team_pick(grade="A+"),
    ]
    out = select_prop_projections(picks)
    assert len(out) == 2
    assert {p.grade for p in out} == {"A+", "A"}
    assert all(p.market_type in PROP_MARKETS for p in out)


def test_select_sorts_aplus_before_a():
    picks = [
        _prop(grade="A", player="B"),
        _prop(grade="A+", player="A"),
    ]
    out = select_prop_projections(picks)
    assert out[0].grade == "A+"
    assert out[1].grade == "A"


def test_select_returns_empty_when_no_props_qualify():
    picks = [_team_pick(grade="A+"), _prop(grade="B")]
    assert select_prop_projections(picks) == []


# ------------------------------------------------ row rendering


def test_row_to_text_matches_brand_spec():
    pick = _prop(
        market="HR", grade="A+", expected="0.82",
        player="Aaron Judge",
        read="Barrel rate +5pp last 2 weeks; fastball pitcher; wind out",
    )
    rows = build_prop_rows([pick])
    assert len(rows) == 1
    row = rows[0]
    assert row.to_text() == (
        "Aaron Judge | Home Runs | 0.82 | A+ | "
        "Barrel rate +5pp last 2 weeks; fastball pitcher; wind out"
    )


def test_market_label_maps_all_prop_markets():
    # Sanity: every market in PROP_MARKETS has a plural label.
    for m in PROP_MARKETS:
        assert m in PROP_MARKET_LABEL


def test_player_name_uses_metadata_when_set():
    p = _prop(player="Aaron Judge")
    assert _player_name(p) == "Aaron Judge"


def test_player_name_falls_back_to_selection_parse():
    """When metadata lacks player_name we split on the over/under marker."""
    p = Pick(
        sport="MLB", market_type="HR", selection="Shohei Ohtani Over 0.5",
        line=Line(odds=+250), grade="A+", metadata={},
    )
    assert _player_name(p) == "Shohei Ohtani"


def test_projected_value_uses_expected_value_decimal_quantize():
    p = _prop(expected="0.8234")
    row = build_prop_rows([p])[0]
    assert row.projected_value == "0.82"


def test_key_read_falls_back_to_factual_grade_line():
    """Phase 31: the empty-read fallback no longer says
    "No analytical delta recorded." It synthesizes a short factual
    Grade/edge sentence so every row still reads like Facts Not
    Feelings."""
    p = _prop(read="")
    row = build_prop_rows([p])[0]
    assert "No analytical delta recorded" not in row.key_read
    assert "Grade" in row.key_read


# ------------------------------------------------ section text


def test_render_section_header_and_table():
    picks = [
        _prop(player="Aaron Judge", market="HR", expected="0.82", grade="A+",
              read="Barrel rate +5pp last 2 weeks"),
        _prop(player="Gerrit Cole", market="K", expected="7.4", grade="A",
              read="Opposing lineup K rate 26%"),
    ]
    text = render_prop_section(picks, date_str="2026-04-22T16:00:00")
    assert text.startswith("Player Prop Projections -- April 22\n")
    # Phase 31: aligned text-table with "Proj" / "Gr" short headers so
    # the section fits an 80-col email client.
    assert "Player" in text and "Market" in text
    assert "Proj" in text and "Gr" in text and "Key Read" in text
    # Divider line + player row still pipe-delimited for legacy parsers.
    assert "Aaron Judge" in text and "Home Runs" in text and "0.82" in text
    assert "Barrel rate +5pp last 2 weeks" in text
    # Projected value is quantized to two decimals so columns line up.
    # Phase 31: cells are width-padded for alignment so we check by
    # substring on each cell rather than exact whitespace.
    assert "Gerrit Cole" in text and "Strikeouts" in text
    assert "7.40" in text
    assert "Opposing lineup K rate 26%" in text


def test_render_section_empty_when_no_props():
    assert render_prop_section([_team_pick(grade="A+")], date_str="2026-04-22") == ""


def test_section_never_uses_top_n_or_dfs_language():
    picks = [_prop() for _ in range(6)]
    text = render_prop_section(picks, date_str="2026-04-22")
    lowered = text.lower()
    for bad in ("top 10", "top 5", "dfs", "draftkings", "fanduel", "lineup optimizer"):
        assert bad not in lowered


def test_section_hides_unit_edge_and_kelly():
    """Section is free content -- must not leak premium numbers."""
    picks = [_prop()]
    text = render_prop_section(picks, date_str="2026-04-22")
    assert "1u" not in text
    assert "unit" not in text.lower()
    assert "Kelly" not in text
    assert "Edge:" not in text
    assert "%" not in text


# ------------------------------------------------ card integration


def _public_card(card_type, picks):
    return PostingFormatter.build_card(
        card_type=card_type,
        picks=picks,
        public_mode=True,
        ledger_stats=_ledger_zero(),
        generated_at="2026-04-22T11:00:00",
    )


def test_daily_edge_card_carries_prop_section():
    picks = [
        _team_pick(grade="A+"),           # goes in main Top-5 block
        _prop(grade="A+"),                # goes in Player Prop Projections
    ]
    card = _public_card("daily_edge", picks)
    assert "player_prop_projections" in card
    assert len(card["player_prop_projections"]["picks"]) == 1
    assert "Home Runs" in card["player_prop_projections"]["text"]


def test_spotlight_card_carries_prop_section():
    picks = [
        _team_pick(grade="A+"),
        _prop(grade="A+"),
    ]
    card = _public_card("spotlight", picks)
    assert "player_prop_projections" in card


def test_non_daily_cards_do_not_carry_prop_section():
    for ct in ("the_ledger", "evening_edge", "overseas_edge"):
        card = _public_card(ct, [_prop(grade="A+")])
        assert "player_prop_projections" not in card


def test_card_does_not_carry_section_when_no_props_qualify():
    card = _public_card("daily_edge", [_team_pick(grade="A+")])
    assert "player_prop_projections" not in card


# ------------------------------------------------ X-formatter end-to-end


def test_x_render_includes_prop_section_between_picks_and_tagline():
    card = _public_card("daily_edge", [_team_pick(grade="A+"), _prop(grade="A+")])
    text = format_card(card)
    # Each landmark appears exactly once and in the expected order:
    pick_pos = text.index("LAA @ NYY - Home Run")   # main pick block
    prop_pos = text.index("Player Prop Projections")
    disclaimer_pos = text.index(DISCLAIMER_TEXT)
    footer_pos = text.index("1-800-GAMBLER")
    hashtags_pos = text.index("#FactsNotFeelings")
    assert pick_pos < prop_pos < disclaimer_pos < hashtags_pos
    assert pick_pos < prop_pos < footer_pos < hashtags_pos


def test_single_footer_invariant_survives_prop_section():
    """Adding the prop section must not duplicate the Season Ledger
    footer / 1-800-GAMBLER line / disclaimer."""
    card = _public_card(
        "daily_edge",
        [_team_pick(grade="A+"), _prop(grade="A+"), _prop(grade="A", player="G. Cole")],
    )
    text = format_card(card)
    assert text.count(DISCLAIMER_TEXT) == 1
    assert text.count("1-800-GAMBLER") == 1
    assert text.count("Bet within your means") == 1


def test_prop_section_text_passes_compliance():
    card = _public_card("daily_edge", [_team_pick(grade="A+"), _prop(grade="A+")])
    text = format_card(card)
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations


def test_prop_section_survives_when_only_props_qualify():
    """Edge case: daily_edge with ONLY A+ props and no team picks.
    filter_daily_edge keeps the props, so the main block has prop rows
    AND the parallel prop section also renders -- that's intended; the
    two sections show the same data from different angles."""
    card = _public_card("daily_edge", [_prop(grade="A+")])
    text = format_card(card)
    assert "Player Prop Projections" in text
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations
