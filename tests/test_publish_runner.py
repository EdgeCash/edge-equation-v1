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


def test_publish_daily_edge_non_dry_run_simulates(tmp_path, monkeypatch):
    # Redirect the X failsafe file output to tmp_path so the test never
    # writes under the repo's data/ directory.
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path))
    # Prevent SMTP from being auto-configured
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_TO", raising=False)

    results = publish_daily_edge(dry_run=False, run_datetime=RUN)
    assert len(results) == 3
    by_target = {r.target: r for r in results}

    # X fails because no real credentials are present, but the failsafe
    # captures the intended post to a local file.
    x = by_target["x"]
    assert x.success is False
    assert x.failsafe_triggered is True
    assert x.failsafe_detail and "file=" in x.failsafe_detail

    # Discord and Email are still stubs that simulate success.
    for target in ("discord", "email"):
        r = by_target[target]
        assert r.success is True
        assert r.message_id and r.message_id.startswith(f"{target}-")


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
