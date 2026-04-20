from datetime import datetime

from edge_equation.publishing.publish_runner import (
    publish_daily_edge,
    publish_evening_edge,
    publish_card,
)
from edge_equation.publishing.base_publisher import PublishResult


RUN = datetime(2026, 4, 20, 9, 0, 0)


def test_publish_daily_edge_dry_run_returns_three_results():
    results = publish_daily_edge(dry_run=True, run_datetime=RUN)
    assert len(results) == 3
    targets = {r.target for r in results}
    assert targets == {"x", "discord", "email"}
    for r in results:
        assert isinstance(r, PublishResult)
        assert r.success is True
        assert r.message_id == "dry-run"


def test_publish_evening_edge_dry_run_returns_three_results():
    results = publish_evening_edge(dry_run=True, run_datetime=RUN)
    assert len(results) == 3
    for r in results:
        assert r.success is True
        assert r.message_id == "dry-run"


def test_publish_daily_edge_non_dry_run_simulates():
    results = publish_daily_edge(dry_run=False, run_datetime=RUN)
    assert len(results) == 3
    for r in results:
        assert r.success is True
        # Simulated send returns a fake ID specific to target
        assert r.message_id is not None
        assert r.message_id != "dry-run"
        assert r.message_id.startswith(f"{r.target}-")


def test_publish_card_accepts_arbitrary_payload():
    # Verify the generic publish_card helper works for non-scheduler cards
    card = {
        "card_type": "custom",
        "headline": "Test",
        "subhead": "sub",
        "picks": [],
        "tagline": "Facts. Not Feelings.",
    }
    results = publish_card(card, dry_run=True)
    assert len(results) == 3
    for r in results:
        assert r.success is True
