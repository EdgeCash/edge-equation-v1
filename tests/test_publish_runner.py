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


def test_publish_daily_edge_non_dry_run_without_creds_routes_to_failsafe(tmp_path, monkeypatch):
    # Redirect every failsafe to tmp_path so the test never writes under the
    # repo's data/ directory, and disable auto-SMTP failsafe.
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path))
    for v in ("SMTP_HOST", "SMTP_FROM", "SMTP_TO", "DISCORD_WEBHOOK_URL",
              "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
              "EMAIL_TO"):
        monkeypatch.delenv(v, raising=False)

    results = publish_daily_edge(dry_run=False, run_datetime=RUN)
    assert len(results) == 3
    by_target = {r.target: r for r in results}

    # Every target fails (no real credentials available) but each failsafe
    # captures the intended post to disk so nothing is lost.
    for target in ("x", "discord", "email"):
        r = by_target[target]
        assert r.success is False
        assert r.failsafe_triggered is True
        assert r.failsafe_detail and "file=" in r.failsafe_detail

    # One failsafe file per target should now be on disk.
    files = list(tmp_path.iterdir())
    targets_written = {f.name.split("-")[0] for f in files}
    assert targets_written == {"x", "discord", "email"}


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
