"""
Phase 26b UX fix: Player Prop Projections and Grade Track Record
sections always render their header in the premium email, even when
there's no content. Empty-state lines explain why -- subscribers can't
mistake a thin-slate day for a missing feature.
"""
from decimal import Decimal

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import format_premium_daily


def _team(grade="A+"):
    return Pick(
        sport="MLB", market_type="ML", selection="NYY",
        line=Line(odds=-115),
        fair_prob=Decimal("0.62"), edge=Decimal("0.09"),
        kelly=Decimal("0.04"), grade=grade, game_id="G-1",
        metadata={"home_team": "NYY", "away_team": "BOS"},
    )


def test_player_prop_section_header_always_renders():
    """Even with zero prop picks, the section header + empty-state line
    must appear so subscribers see the slot exists."""
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[_team()],
        generated_at="2026-04-22T10:00:00",
    )
    body = format_premium_daily(card)
    assert "=== PLAYER PROP PROJECTIONS ===" in body
    assert "no qualifying props today" in body


def test_grade_track_record_header_always_renders():
    """Same invariant for track record -- fresh-deploy days still show
    the section with a 'building' empty-state rather than silently
    omitting it."""
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[_team()],
        generated_at="2026-04-22T10:00:00",
    )
    body = format_premium_daily(card)
    assert "=== GRADE TRACK RECORD ===" in body
    assert "building" in body.lower()


def test_cold_cache_premium_email_shows_all_section_headers():
    """The zero-everything scenario (cold cache, no picks) must still
    render every section header -- the promise of the premium email
    is that every slot is always there."""
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[],          # no picks of any kind
        generated_at="2026-04-22T10:00:00",
    )
    body = format_premium_daily(card)
    for header in (
        "=== GRADE TRACK RECORD ===",
        "=== DAILY EDGE",
        "=== PLAYER PROP PROJECTIONS ===",
        "=== PARLAY OF THE DAY ===",
        "=== ENGINE HEALTH",
    ):
        assert header in body, f"missing header {header!r}"
    # Apr 26: Spotlight section was removed from the premium email per
    # subscriber feedback (early-MLB Spotlight blocks were noisy and
    # didn't add information). The Spotlight CARD itself (its own
    # 4pm-CT publish) is unaffected; this assertion just guards the
    # premium email render.
    assert "=== SPOTLIGHT ===" not in body


def test_populated_prop_section_overrides_empty_state():
    """When prop picks DO exist, the empty-state line must NOT appear."""
    prop = Pick(
        sport="MLB", market_type="HR",
        selection="Aaron Judge over 0.5",
        line=Line(odds=+280),
        fair_prob=Decimal("0.55"),
        expected_value=Decimal("0.82"),
        edge=Decimal("0.09"),
        kelly=Decimal("0.025"),
        grade="A+", game_id="G-J",
        metadata={
            "home_team": "NYY", "away_team": "BOS",
            "player_name": "Aaron Judge",
            "read_notes": "Barrel rate +5pp last 2 weeks.",
        },
    )
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[prop],
        generated_at="2026-04-22T10:00:00",
    )
    body = format_premium_daily(card)
    assert "=== PLAYER PROP PROJECTIONS ===" in body
    assert "no qualifying props today" not in body
    assert "Aaron Judge | Home Runs" in body
