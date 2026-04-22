"""
Phase 21: ScheduledRunner now supports the full five-window cadence +
public_mode + ledger_stats plumbing into PostingFormatter.build_card.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_LEDGER,
    CARD_TYPE_OVERSEAS_EDGE,
    CARD_TYPE_SPOTLIGHT,
    OVERSEAS_LEAGUES,
    ScheduledRunner,
    VALID_CARD_TYPES,
)
from edge_equation.persistence.db import Database
from edge_equation.posting.ledger import LedgerStats
from edge_equation.publishing.base_publisher import PublishResult


RUN_DT = datetime(2026, 4, 20, 9, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


class _Capturer:
    def __init__(self):
        self.calls = []

    def publish_card(self, card, dry_run=False):
        self.calls.append({"card": card, "dry_run": dry_run})
        return PublishResult(success=True, target="x", message_id="1")


def test_valid_card_types_includes_phase21_additions():
    for ct in (CARD_TYPE_LEDGER, CARD_TYPE_SPOTLIGHT, CARD_TYPE_OVERSEAS_EDGE):
        assert ct in VALID_CARD_TYPES


def test_run_accepts_ledger_card_type(conn):
    stats = LedgerStats(
        wins=10, losses=8, pushes=1,
        units_net=Decimal("2.50"), roi_pct=Decimal("1.5"), total_plays=19,
    )
    capt = _Capturer()
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_LEDGER,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["MLB"],
        publish=True,
        dry_run=True,
        prefer_mock=True,
        public_mode=True,
        ledger_stats=stats,
        publishers=[capt],
    )
    assert summary.card_type == CARD_TYPE_LEDGER
    assert summary.new_slate is True
    assert len(capt.calls) == 1
    card = capt.calls[0]["card"]
    # Phase 20 brand footer must land in the tagline when ledger_stats flows through
    assert "Season Ledger:" in (card.get("tagline") or "")
    assert "Bet within your means" in (card.get("tagline") or "")


def test_run_accepts_spotlight_card_type(conn):
    capt = _Capturer()
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_SPOTLIGHT,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["MLB"],
        publish=True,
        dry_run=True,
        prefer_mock=True,
        public_mode=True,
        publishers=[capt],
    )
    assert summary.card_type == CARD_TYPE_SPOTLIGHT
    assert capt.calls[0]["card"]["headline"] == "Spotlight"


def test_run_accepts_overseas_card_type(conn):
    # Only KBO / NPB have mock ingestion sources; EPL/UCL need the odds API.
    capt = _Capturer()
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["KBO", "NPB"],
        publish=True,
        dry_run=True,
        prefer_mock=True,
        public_mode=True,
        publishers=[capt],
    )
    assert summary.card_type == CARD_TYPE_OVERSEAS_EDGE
    card = capt.calls[0]["card"]
    assert card["headline"] == "Overseas Edge"


def test_public_mode_strips_edge_on_published_card(conn):
    capt = _Capturer()
    ScheduledRunner.run(
        card_type=CARD_TYPE_SPOTLIGHT,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["MLB"],
        publish=True,
        dry_run=True,
        prefer_mock=True,
        public_mode=True,
        publishers=[capt],
    )
    # PublicModeSanitizer removes or nulls the sensitive summary fields.
    summary = capt.calls[0]["card"].get("summary", {})
    assert summary.get("edge") in (None, "", "0", "0.0")
    assert summary.get("kelly") in (None, "", "0", "0.0")


def test_overseas_leagues_constant_is_reasonable():
    assert "KBO" in OVERSEAS_LEAGUES
    assert "NPB" in OVERSEAS_LEAGUES
