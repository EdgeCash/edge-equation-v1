"""
Phase 22 end-to-end wiring:
  - load_prior_daily_edge_picks pulls from the most recent daily_edge slate.
  - Evening Edge auto-reads the prior slate when prior_picks isn't supplied.
  - PostingFormatter's spotlight path routes through the Spotlight selector.
  - The public-mode X formatter emits the exact 4-line brand block,
    uses the two whitelisted hashtags only, and passes compliance_test.
  - XPublisher's compliance gate fires the failsafe on a bad card.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from edge_equation.compliance import compliance_test
from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_EVENING,
    ScheduledRunner,
    load_prior_daily_edge_picks,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.ledger import LedgerStats
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.x_formatter import PUBLIC_HASHTAGS, format_card
from edge_equation.publishing.x_publisher import XPublisher


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _pick(sport="MLB", game_id="G1", grade="A+", edge="0.12",
          market="ML", selection="Home", odds=-110, home="NYY", away="BOS"):
    return Pick(
        sport=sport,
        market_type=market,
        selection=selection,
        line=Line(odds=odds),
        fair_prob=Decimal("0.55"),
        edge=Decimal(edge),
        kelly=Decimal("0.02"),
        grade=grade,
        game_id=game_id,
        metadata={"home_team": home, "away_team": away, "read_notes": "Analytical delta."},
    )


def _ledger_zero():
    return LedgerStats(
        wins=0, losses=0, pushes=0,
        units_net=Decimal("0"), roi_pct=Decimal("0.0"), total_plays=0,
    )


# -------------------------------------------------- prior-slate loader


def test_load_prior_returns_empty_on_fresh_db(conn):
    assert load_prior_daily_edge_picks(conn) == []


def test_load_prior_returns_most_recent_daily_edge(conn):
    prior_pick = _pick(game_id="PRIOR")
    SlateStore.insert(conn, SlateRecord(
        slate_id="daily_edge_20260419",
        generated_at="2026-04-19T11:00:00",
        sport=None, card_type=CARD_TYPE_DAILY, metadata={},
    ))
    PickStore.insert_many(conn, [prior_pick],
                          slate_id="daily_edge_20260419",
                          recorded_at="2026-04-19T11:00:00")
    out = load_prior_daily_edge_picks(conn)
    assert len(out) == 1
    assert out[0].game_id == "PRIOR"


def test_load_prior_skips_slates_at_or_after_cutoff(conn):
    future = SlateRecord(
        slate_id="daily_edge_20260420",
        generated_at="2026-04-20T11:00:00",
        sport=None, card_type=CARD_TYPE_DAILY, metadata={},
    )
    past = SlateRecord(
        slate_id="daily_edge_20260419",
        generated_at="2026-04-19T11:00:00",
        sport=None, card_type=CARD_TYPE_DAILY, metadata={},
    )
    SlateStore.insert(conn, future)
    SlateStore.insert(conn, past)
    PickStore.insert_many(conn, [_pick(game_id="FUTURE")],
                          slate_id=future.slate_id, recorded_at=future.generated_at)
    PickStore.insert_many(conn, [_pick(game_id="PAST")],
                          slate_id=past.slate_id, recorded_at=past.generated_at)
    out = load_prior_daily_edge_picks(
        conn, before=datetime(2026, 4, 20, 10, 0, 0)
    )
    # Cutoff at 10:00 excludes the 11:00 future slate, returns the 19th's picks.
    assert [p.game_id for p in out] == ["PAST"]


# -------------------------------------------------- evening_edge stability


def test_evening_edge_line_movement_is_material():
    a = _pick(odds=-110)
    b = _pick(odds=-125)  # same game/market/selection, different price
    assert PostingFormatter.evening_edge_is_stable([a], [b]) is False


def test_evening_edge_identical_state_is_stable():
    a = _pick()
    b = _pick()
    assert PostingFormatter.evening_edge_is_stable([a], [b]) is True


def test_evening_edge_injury_flag_is_material():
    a = _pick()
    b = Pick(
        sport=a.sport, market_type=a.market_type, selection=a.selection,
        line=a.line, fair_prob=a.fair_prob, edge=a.edge, kelly=a.kelly,
        grade=a.grade, game_id=a.game_id,
        metadata={**a.metadata, "injury_flag": True},
    )
    assert PostingFormatter.evening_edge_is_stable([b], [a]) is False


# -------------------------------------------------- spotlight routing


def test_spotlight_card_picks_the_trending_game():
    top = _pick(game_id="BIG", grade="A+", edge="0.15", home="NYY", away="BOS")
    small = _pick(game_id="SMALL", grade="A", edge="0.06")
    card = PostingFormatter.build_card(
        card_type="spotlight",
        picks=[top, small],
        public_mode=True,
        ledger_stats=_ledger_zero(),
    )
    game_ids = {p["game_id"] for p in card["picks"]}
    assert game_ids == {"BIG"}


def test_spotlight_with_no_qualifying_game_emits_empty_short_card():
    card = PostingFormatter.build_card(
        card_type="spotlight",
        picks=[_pick(grade="B")],
        public_mode=True,
        ledger_stats=_ledger_zero(),
    )
    assert card["picks"] == []
    assert "Spotlight bar" in card["subhead"]


# -------------------------------------------------- public-mode X formatter


def _public_card_with(picks):
    return PostingFormatter.build_card(
        card_type="daily_edge",
        picks=picks,
        public_mode=True,
        ledger_stats=_ledger_zero(),
        skip_filter=True,
    )


def test_public_formatter_uses_brand_4_line_block():
    card = _public_card_with([_pick(grade="A+", market="HR",
                                    selection="Judge over 0.5",
                                    odds=+280)])
    text = format_card(card)
    assert "BOS @ NYY - Home Run" in text
    assert "Market Consensus: Judge over 0.5 (+280)" in text
    assert "EE Projection: Grade A+" in text
    assert "Read: Analytical delta." in text


def test_public_formatter_emits_only_whitelisted_hashtags():
    card = _public_card_with([_pick()])
    text = format_card(card)
    assert PUBLIC_HASHTAGS[0] in text
    assert PUBLIC_HASHTAGS[1] in text
    # No other # tokens in the text (disclaimer carries none either).
    stray = [tok for tok in text.split() if tok.startswith("#") and tok not in PUBLIC_HASHTAGS]
    assert stray == []


def test_public_formatter_includes_ledger_footer_and_disclaimer():
    card = _public_card_with([_pick()])
    text = format_card(card)
    assert "Season Ledger:" in text
    assert "Call 1-800-GAMBLER" in text
    assert DISCLAIMER_TEXT in text


def test_public_card_text_passes_compliance():
    card = _public_card_with([_pick()])
    text = format_card(card)
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations


# -------------------------------------------------- publisher compliance gate


def test_publisher_compliance_gate_routes_failing_card_to_failsafe():
    """A card the formatter can render but the compliance_test blocks
    must trigger the failsafe and NOT hit the X API."""
    delivered = []

    class _Fail:
        def deliver(self, subject, body, target="x", now=None):
            delivered.append((subject, body))
            return "file=/tmp/x.txt"

    # Build a card without the disclaimer to simulate a misconfigured
    # public post that still somehow asks for the ledger footer. Manually
    # inject the disclaimer into the tagline so the gate activates, but
    # strip the footer so compliance fails.
    bad_card = PostingFormatter.build_card(
        card_type="daily_edge",
        picks=[_pick()],
        public_mode=True,
        ledger_stats=_ledger_zero(),
        skip_filter=True,
    )
    # Re-tagline to drop the Season Ledger -> compliance_test should fail.
    bad_card["tagline"] = DISCLAIMER_TEXT

    pub = XPublisher(
        api_key="CK", api_secret="CS",
        access_token="AT", access_token_secret="ATS",
        failsafe=_Fail(),
    )
    result = pub.publish_card(bad_card, dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is True
    assert "compliance" in (result.error or "")
    assert delivered, "failsafe should have received the blocked post"
