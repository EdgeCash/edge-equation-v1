"""Brand text-play format tests."""
from decimal import Decimal
import pytest

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.play_text import (
    MARKET_LABEL,
    PlayTextInputs,
    build_play_text_inputs,
    render_play,
    render_plays,
)


def _pick(
    market_type="HR",
    selection="Judge over 0.5",
    odds=280,
    number="0.5",
    grade="A+",
    home_team="NYY",
    away_team="LAA",
    game_id="MLB-2026-04-22-LAA-NYY",
):
    line_number = Decimal(number) if number else None
    return Pick(
        sport="MLB",
        market_type=market_type,
        selection=selection,
        line=Line(odds=odds, number=line_number),
        fair_prob=None,
        expected_value=Decimal('0.15'),
        edge=Decimal('0.07'),
        kelly=Decimal('0.01'),
        grade=grade,
        realization=68,
        game_id=game_id,
        metadata={"home_team": home_team, "away_team": away_team},
    )


# ------------------------------------------------ render_play shape


def test_render_matches_brand_format():
    inputs = PlayTextInputs(
        away_team="LAA", home_team="NYY",
        market_label="Home Run",
        selection_label="Judge over 0.5",
        odds_str="+280",
        grade="A+",
        read_notes="Barrel rate up 5pp last 2 weeks; fastball pitcher; wind out",
    )
    text = render_play(inputs)
    expected = (
        "LAA @ NYY - Home Run\n"
        "Market Consensus: Judge over 0.5 (+280)\n"
        "EE Projection: Grade A+\n"
        "Read: Barrel rate up 5pp last 2 weeks; fastball pitcher; wind out"
    )
    assert text == expected


def test_render_without_odds_omits_parens():
    inputs = PlayTextInputs(
        away_team="A", home_team="B",
        market_label="Moneyline",
        selection_label="A",
        odds_str="",
        grade="B",
        read_notes="even money, light action",
    )
    text = render_play(inputs)
    assert "Market Consensus: A\n" in text


def test_render_missing_read_notes_defaults_line():
    inputs = PlayTextInputs(
        away_team="A", home_team="B",
        market_label="Moneyline",
        selection_label="A",
        odds_str="-110",
        grade="C",
        read_notes="",
    )
    text = render_play(inputs)
    assert "Read: No analytical delta recorded." in text


def test_render_matchup_collapses_when_team_missing():
    inputs = PlayTextInputs(
        away_team="", home_team="NYY",
        market_label="Moneyline", selection_label="NYY",
        odds_str="-150", grade="A", read_notes="x",
    )
    text = render_play(inputs)
    assert text.startswith("NYY - Moneyline\n")


# ------------------------------------------------ build_play_text_inputs


def test_build_inputs_from_pick_uses_metadata_teams():
    pick = _pick()
    inputs = build_play_text_inputs(pick)
    assert inputs.home_team == "NYY"
    assert inputs.away_team == "LAA"
    assert inputs.market_label == "Home Run"
    # Selection already contains "0.5" so we don't append the line twice.
    assert inputs.selection_label == "Judge over 0.5"
    assert inputs.odds_str == "+280"
    assert inputs.grade == "A+"


def test_build_inputs_allows_explicit_teams_override():
    pick = _pick(home_team="YYY", away_team="ZZZ")
    inputs = build_play_text_inputs(pick, home_team="NYY", away_team="LAA")
    assert inputs.home_team == "NYY"
    assert inputs.away_team == "LAA"


def test_build_inputs_appends_line_number_when_missing_from_selection():
    pick = _pick(selection="Judge over", number="0.5")
    inputs = build_play_text_inputs(pick)
    assert inputs.selection_label == "Judge over 0.5"


def test_build_inputs_negative_odds():
    pick = _pick(odds=-110)
    inputs = build_play_text_inputs(pick)
    assert inputs.odds_str == "-110"


def test_market_label_table_covers_common_markets():
    for code in ("ML", "Run_Line", "Total", "HR", "K",
                 "Passing_Yards", "Points", "Rebounds", "SOG", "BTTS"):
        assert code in MARKET_LABEL


# ------------------------------------------------ no forbidden language


def test_rendered_text_does_not_leak_edge_or_kelly():
    pick = _pick()
    inputs = build_play_text_inputs(pick, read_notes="wind out, elite barrel rate")
    text = render_play(inputs)
    for bad in ("edge", "kelly", "bet", "pick", "lock", "smash",
                "value", "sharp"):
        assert bad.lower() not in text.lower(), f"found {bad!r} in rendered play"


# ------------------------------------------------ render_plays (multi-pick)


def test_render_plays_separates_by_blank_line():
    a = PlayTextInputs(
        away_team="LAA", home_team="NYY", market_label="Home Run",
        selection_label="Judge over 0.5", odds_str="+280", grade="A+",
        read_notes="one",
    )
    b = PlayTextInputs(
        away_team="BOS", home_team="DET", market_label="Moneyline",
        selection_label="BOS", odds_str="-132", grade="A",
        read_notes="two",
    )
    text = render_plays([a, b])
    assert "\n\n" in text
    assert text.count("EE Projection:") == 2
