import pytest

from edge_equation.publishing.x_formatter import (
    StandardFormatter,
    PremiumFormatter,
    format_card,
    PREMIUM_MAX_LEN,
    STANDARD_MAX_LEN,
    GRADE_BADGE,
    CARD_TYPE_ICON,
    DIVIDER,
)


def _card(**overrides):
    base = {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's model-graded plays.",
        "picks": [
            {"sport": "MLB", "market_type": "ML", "selection": "BOS",
             "grade": "A", "edge": "0.049167", "fair_prob": "0.553412",
             "kelly": "0.0085", "line": {"odds": -132, "number": None},
             "game_id": "MLB-2026-04-20-DET-BOS"},
            {"sport": "MLB", "market_type": "Total", "selection": "Over 9.5",
             "grade": "C", "edge": None, "expected_value": "9.78",
             "line": {"odds": -110, "number": "9.5"},
             "game_id": "MLB-2026-04-20-DET-BOS"},
        ],
        "tagline": "Facts. Not Feelings.",
        "summary": {"grade": "A", "edge": "0.049167", "kelly": "0.0085"},
        "generated_at": "2026-04-20T09:00:00",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------- StandardFormatter


def test_standard_headline_and_bullets():
    text = StandardFormatter.format_card(_card())
    assert "🎯" in text
    assert "Daily Edge" in text
    assert "BOS" in text
    assert "Over 9.5" in text


def test_standard_includes_tagline():
    text = StandardFormatter.format_card(_card())
    assert "Facts. Not Feelings." in text


def test_standard_respects_280_cap():
    text = StandardFormatter.format_card(_card())
    assert len(text) <= STANDARD_MAX_LEN


def test_standard_truncates_with_ellipsis_when_oversized():
    huge = _card(headline="H" * 500)
    text = StandardFormatter.format_card(huge)
    assert len(text) <= STANDARD_MAX_LEN
    assert text.endswith("…")


def test_standard_limits_to_two_picks():
    big_card = _card(picks=_card()["picks"] + [
        {"market_type": "ML", "selection": f"PICK_{i}", "grade": "B", "edge": None}
        for i in range(5)
    ])
    text = StandardFormatter.format_card(big_card)
    # Only first two picks in the standard format
    assert "PICK_0" not in text  # not among the first two


def test_standard_renders_edge_as_percentage():
    text = StandardFormatter.format_card(_card())
    assert "4.92%" in text


# -------------------------------------------------------- PremiumFormatter


def test_premium_uppercases_headline():
    text = PremiumFormatter.format_card(_card())
    assert "DAILY EDGE" in text


def test_premium_includes_subhead():
    text = PremiumFormatter.format_card(_card())
    assert "Today's model-graded plays." in text


def test_premium_has_section_dividers():
    text = PremiumFormatter.format_card(_card())
    assert DIVIDER in text
    # Summary block + each pick + footer => multiple dividers
    assert text.count(DIVIDER) >= 3


def test_premium_summary_reports_slate_stats():
    text = PremiumFormatter.format_card(_card())
    assert "Slate:" in text
    assert "Top grade:" in text
    assert "Max edge:" in text
    assert "Max Kelly:" in text


def test_premium_pick_block_includes_market_and_selection():
    text = PremiumFormatter.format_card(_card())
    assert "MLB · ML" in text
    assert "BOS" in text


def test_premium_pick_block_includes_stats_line():
    text = PremiumFormatter.format_card(_card())
    assert "fair" in text
    assert "edge" in text
    assert "Kelly" in text


def test_premium_totals_line_includes_number_and_odds():
    text = PremiumFormatter.format_card(_card())
    assert "9.5 @ -110" in text


def test_premium_ml_line_has_just_odds():
    text = PremiumFormatter.format_card(_card())
    assert "-132" in text


def test_premium_grade_badge_rendered():
    text = PremiumFormatter.format_card(_card())
    assert GRADE_BADGE["A"] in text
    assert GRADE_BADGE["C"] in text


def test_premium_footer_has_tagline_and_timestamp():
    text = PremiumFormatter.format_card(_card())
    assert "Facts. Not Feelings." in text
    assert "generated 2026-04-20T09:00:00" in text


def test_premium_empty_picks_still_renders():
    text = PremiumFormatter.format_card(_card(picks=[]))
    assert "DAILY EDGE" in text
    assert "Slate: 0 picks" in text


def test_premium_single_pick_reports_singular():
    text = PremiumFormatter.format_card(_card(picks=[_card()["picks"][0]]))
    assert "Slate: 1 pick" in text
    assert "Slate: 1 picks" not in text


def test_premium_under_25k_for_typical_slate():
    text = PremiumFormatter.format_card(_card())
    assert len(text) < PREMIUM_MAX_LEN


def test_premium_truncates_enormous_card():
    huge_picks = [_card()["picks"][0] for _ in range(10000)]
    text = PremiumFormatter.format_card(_card(picks=huge_picks))
    assert len(text) <= PREMIUM_MAX_LEN


def test_premium_pick_numbering_consistent():
    text = PremiumFormatter.format_card(_card())
    assert "Pick 1/2" in text
    assert "Pick 2/2" in text


def test_premium_icon_varies_by_card_type():
    daily_text = PremiumFormatter.format_card(_card(card_type="daily_edge"))
    overseas_text = PremiumFormatter.format_card(_card(card_type="overseas_edge"))
    assert CARD_TYPE_ICON["daily_edge"] in daily_text
    assert CARD_TYPE_ICON["overseas_edge"] in overseas_text
    assert daily_text != overseas_text


def test_premium_hfa_and_decay_surface_when_present():
    card = _card()
    card["picks"][0]["hfa_value"] = "1.000000"
    card["picks"][0]["decay_halflife_days"] = "277.258872"
    text = PremiumFormatter.format_card(card)
    assert "HFA 1.000000" in text
    assert "decay τ½ 277.258872d" in text


# --------------------------------------------------------- format_card


def test_format_card_defaults_to_premium():
    text = format_card(_card())
    assert DIVIDER in text


def test_format_card_standard_respects_cap():
    text = format_card(_card(), style="standard")
    assert len(text) <= STANDARD_MAX_LEN


def test_format_card_unknown_style_raises():
    with pytest.raises(ValueError, match="Unknown style"):
        format_card(_card(), style="weird")


def test_grade_badge_covers_all_grades():
    for g in ("A+", "A", "B", "C", "D", "F"):
        assert g in GRADE_BADGE


def test_card_type_icon_covers_all_templates():
    for ct in ("daily_edge", "evening_edge", "overseas_edge",
               "highlighted_game", "model_highlight", "sharp_signal", "the_outlier"):
        assert ct in CARD_TYPE_ICON
