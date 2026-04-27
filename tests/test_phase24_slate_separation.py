"""
Phase 24 — slate separation + Ledger recap + enriched premium email.

1. Slate separation: KBO/NPB/soccer MUST NOT appear on Daily Edge /
   Ledger / Spotlight / Evening Edge. Overseas Edge admits ONLY the
   overseas leagues. The runner silently strips off-slate leagues (with
   an audit log) rather than raising, because a stray --leagues input
   shouldn't crash the whole cadence.

2. Ledger recap: the 9am "The Ledger" card body is a cross-slot recap
   of every projection we posted publicly yesterday. The Season
   Ledger footer (all-time) still appears exactly once at the bottom.

3. Premium email enrichment: Yesterday's Ledger recap + Daily Edge full
   slate + Spotlight deep dive + Player Prop Projections + Parlay +
   MVS (when firing) + Engine Health all land in one subscriber email.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import json
from unittest.mock import patch

import pytest

from edge_equation.__main__ import main
from edge_equation.compliance import compliance_test
from edge_equation.compliance.disclaimer import DISCLAIMER_TEXT
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_LEDGER,
    CARD_TYPE_OVERSEAS_EDGE,
    DOMESTIC_LEAGUES,
    LEAGUES_FOR_CARD_TYPE,
    OVERSEAS_LEAGUES,
    ScheduledRunner,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.ledger import LedgerStats
from edge_equation.posting.ledger_recap import (
    collect_yesterday_recap,
    format_daily_recap,
    recap_to_public_dict,
)
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import format_premium_daily
from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.x_formatter import format_card


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ------------------------------------------------ slate separation

def test_domestic_and_overseas_lists_are_disjoint():
    assert set(DOMESTIC_LEAGUES).isdisjoint(set(OVERSEAS_LEAGUES))
    assert "KBO" in OVERSEAS_LEAGUES
    assert "MLB" in DOMESTIC_LEAGUES


def test_leagues_for_card_type_maps_every_card():
    assert LEAGUES_FOR_CARD_TYPE[CARD_TYPE_DAILY] == DOMESTIC_LEAGUES
    assert LEAGUES_FOR_CARD_TYPE[CARD_TYPE_LEDGER] == DOMESTIC_LEAGUES
    assert LEAGUES_FOR_CARD_TYPE[CARD_TYPE_OVERSEAS_EDGE] == OVERSEAS_LEAGUES


def test_runner_strips_overseas_leagues_from_daily_edge(conn, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="edge-equation.runner"):
        summary = ScheduledRunner.run(
            card_type=CARD_TYPE_DAILY,
            conn=conn,
            run_datetime=datetime(2026, 4, 22, 11, 0),
            leagues=["MLB", "KBO", "NPB"],   # off-slate leagues mixed in
            prefer_mock=True,
        )
    # Only MLB survives; the runner silently strips KBO/NPB.
    assert summary.leagues == ("MLB",)
    assert any("dropped leagues" in r.message for r in caplog.records)


def test_runner_strips_domestic_leagues_from_overseas_edge(conn):
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE,
        conn=conn,
        run_datetime=datetime(2026, 4, 22, 23, 0),
        leagues=["MLB", "KBO"],
        prefer_mock=True,
    )
    assert summary.leagues == ("KBO",)


def test_runner_raises_when_no_leagues_survive(conn):
    with pytest.raises(ValueError, match="No leagues left"):
        ScheduledRunner.run(
            card_type=CARD_TYPE_DAILY,
            conn=conn,
            run_datetime=datetime(2026, 4, 22),
            leagues=["KBO"],    # all off-slate -> nothing left
            prefer_mock=True,
        )


def test_daily_edge_default_does_not_include_overseas(conn):
    # Leagues=None -> CLI path falls back to DOMESTIC_LEAGUES.
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY,
        conn=conn,
        run_datetime=datetime(2026, 4, 22),
        leagues=None,     # force fallback
        prefer_mock=True,
    )
    assert "KBO" not in summary.leagues
    assert "NPB" not in summary.leagues
    assert "MLB" in summary.leagues


# ------------------------------------------------ Ledger recap


def _dom_pick(game_id="G1", grade="A+", realization=100):
    return Pick(
        sport="MLB", market_type="ML", selection="NYY",
        line=Line(odds=-115),
        fair_prob=Decimal("0.60"),
        edge=Decimal("0.09"),
        kelly=Decimal("0.03"),
        grade=grade,
        realization=realization,
        game_id=game_id,
        metadata={"home_team": "NYY", "away_team": "BOS"},
    )


def _seed_yesterday_slate(conn, card_type, picks, date_str):
    slate_id = f"{card_type}_{date_str.replace('-', '')}"
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id, generated_at=f"{date_str}T11:00:00",
        sport=None, card_type=card_type, metadata={},
    ))
    PickStore.insert_many(
        conn, picks, slate_id=slate_id, recorded_at=f"{date_str}T11:00:00",
    )
    # Manually set realization (insert_many uses default 47 = pending)
    for p in picks:
        conn.execute(
            "UPDATE picks SET realization = ? WHERE slate_id = ? AND game_id = ? AND selection = ?",
            (p.realization, slate_id, p.game_id, p.selection),
        )
    conn.commit()


def test_collect_yesterday_recap_walks_four_slate_types(conn):
    yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
    _seed_yesterday_slate(conn, "daily_edge",
                          [_dom_pick("DE-1", "A+"), _dom_pick("DE-2", "A", realization=0)],
                          yesterday)
    _seed_yesterday_slate(conn, "spotlight",
                          [_dom_pick("SP-1", "A+", realization=50)], yesterday)
    _seed_yesterday_slate(conn, "evening_edge", [], yesterday)  # stable
    _seed_yesterday_slate(conn, "overseas_edge",
                          [_dom_pick("OV-1", "A+")], yesterday)

    recap = collect_yesterday_recap(conn, run_dt=datetime.utcnow())
    assert len(recap["daily_edge"].picks) == 2
    assert len(recap["spotlight"].picks) == 1
    assert recap["evening_edge"].picks == []
    assert len(recap["overseas_edge"].picks) == 1


def test_recap_text_includes_outcome_labels(conn):
    yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
    _seed_yesterday_slate(conn, "daily_edge",
                          [_dom_pick("DE-W", "A+", realization=100),
                           _dom_pick("DE-L", "A", realization=0),
                           _dom_pick("DE-P", "A", realization=50)],
                          yesterday)

    recap = collect_yesterday_recap(conn, run_dt=datetime.utcnow())
    text = format_daily_recap(recap, run_dt=datetime.utcnow())
    assert "Yesterday's Results" in text
    assert "Win" in text
    assert "Loss" in text
    assert "Push" in text
    assert "Daily Edge: 3 projections posted" in text


def test_recap_text_says_stable_for_empty_evening_edge(conn):
    yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
    _seed_yesterday_slate(conn, "evening_edge", [], yesterday)
    recap = collect_yesterday_recap(conn, run_dt=datetime.utcnow())
    text = format_daily_recap(recap, run_dt=datetime.utcnow())
    assert "Evening Edge: no material update" in text


def test_ledger_card_carries_recap_section():
    card = PostingFormatter.build_card(
        card_type="the_ledger",
        picks=[],
        public_mode=True,
        ledger_stats=LedgerStats(
            wins=68, losses=49, pushes=3,
            units_net=Decimal("8.45"), roi_pct=Decimal("7.0"),
            total_plays=120,
        ),
        daily_recap_text="Yesterday's Results -- April 21\nDaily Edge: 3 projections posted\n",
        generated_at="2026-04-22T09:00:00",
    )
    assert "daily_recap" in card
    text = format_card(card)
    # Recap section lands above the Season Ledger footer.
    recap_pos = text.index("Yesterday's Results")
    footer_pos = text.index("Season Ledger:")
    assert recap_pos < footer_pos


def test_ledger_card_keeps_single_footer_invariant():
    card = PostingFormatter.build_card(
        card_type="the_ledger",
        picks=[],
        public_mode=True,
        ledger_stats=LedgerStats(
            wins=68, losses=49, pushes=3,
            units_net=Decimal("8.45"), roi_pct=Decimal("7.0"),
            total_plays=120,
        ),
        daily_recap_text="Yesterday's Results -- April 21\nDaily Edge: nothing posted.\n",
        generated_at="2026-04-22T09:00:00",
    )
    text = format_card(card)
    assert text.count(DISCLAIMER_TEXT) == 1
    assert text.count("1-800-GAMBLER") == 1
    assert text.count("Bet within your means") == 1


def test_ledger_card_passes_compliance():
    card = PostingFormatter.build_card(
        card_type="the_ledger",
        picks=[],
        public_mode=True,
        ledger_stats=LedgerStats(
            wins=68, losses=49, pushes=3,
            units_net=Decimal("8.45"), roi_pct=Decimal("7.0"),
            total_plays=120,
        ),
        daily_recap_text="Yesterday's Results -- April 21\nDaily Edge: 1 projection posted\n",
        generated_at="2026-04-22T09:00:00",
    )
    text = format_card(card)
    report = compliance_test(text, require_ledger_footer=True)
    assert report.ok is True, report.violations


# ------------------------------------------------ premium email enrichment


def _prop_pick(player="Aaron Judge", market="HR", grade="A+", expected="0.82"):
    return Pick(
        sport="MLB", market_type=market, selection=f"{player} over 0.5",
        line=Line(odds=+280),
        fair_prob=Decimal("0.55"),
        expected_value=Decimal(expected),
        edge=Decimal("0.09"),
        kelly=Decimal("0.025"),
        grade=grade,
        game_id="G-JUDGE",
        metadata={"home_team": "NYY", "away_team": "BOS",
                  "player_name": player, "read_notes": "Data read."},
    )


def test_premium_email_includes_all_seven_sections():
    team = _dom_pick("G-1", "A+")
    prop = _prop_pick()
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[team, prop],
        public_mode=False,
        engine_health={
            "wins": 3, "losses": 2, "pushes": 1, "n": 6, "hit_rate": 0.6,
        },
        daily_recap_text="Yesterday's Results -- April 21\nDaily Edge: 3 projections posted\n",
        generated_at="2026-04-22T11:00:00",
    )
    body = format_premium_daily(card)
    assert "YESTERDAY'S LEDGER" in body
    assert "DAILY EDGE" in body
    # SPOTLIGHT section removed from the premium email Apr 26 (the
    # SPOTLIGHT card's own publish workflow is unaffected).
    assert "SPOTLIGHT" not in body
    assert "PLAYER PROP PROJECTIONS" in body
    assert "PARLAY OF THE DAY" in body
    assert "ENGINE HEALTH" in body


def test_premium_email_prop_section_uses_brand_format():
    prop = _prop_pick(player="Aaron Judge", market="HR", expected="0.82")
    card = PostingFormatter.build_card(
        card_type="premium_daily", picks=[prop],
        generated_at="2026-04-22T11:00:00",
    )
    body = format_premium_daily(card)
    # Exact pipe-separated brand format.
    assert "Aaron Judge | Home Runs | 0.82 | A+" in body
    # No DFS / app mentions, no Top-N language.
    assert "DFS" not in body
    assert "Top 6" not in body
    assert "Top 10" not in body


# ------------------------------------------------ CLI end-to-end


class _FakeSmtp:
    sent = []
    def __init__(self, host, port, **kw): self.host, self.port = host, port
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, m): type(self).sent.append(m)


def _isolate(monkeypatch, tmp_path):
    _FakeSmtp.sent = []
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("SMTP_FROM", "bot@edge.com")
    monkeypatch.setenv("EMAIL_TO", "ProfessorEdgeCash@gmail.com")
    for v in ("X_API_KEY", "X_API_SECRET", "DISCORD_WEBHOOK_URL",
              "THE_ODDS_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_daily_cli_default_leagues_filter_out_overseas(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "domestic.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        code = main([
            "daily", "--db", db_path,
            "--prefer-mock", "--email-preview",
        ])
    cap = capsys.readouterr()
    assert code == 0, cap.err
    payload = json.loads(cap.out)
    # No overseas league survives the default leagues path.
    for league in payload["leagues"]:
        assert league in DOMESTIC_LEAGUES, (
            f"overseas league {league!r} leaked into Daily Edge"
        )


def test_ledger_cli_emits_recap_section(tmp_path, monkeypatch, capsys):
    """Ledger CLI path computes yesterday's recap and surfaces it in
    the rendered email body."""
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "ledger.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        # First: populate yesterday's daily_edge slate
        from edge_equation.persistence.db import Database
        conn = Database.open(db_path)
        Database.migrate(conn)
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
        _seed_yesterday_slate(
            conn, "daily_edge", [_dom_pick("DE-1", "A+", realization=100)],
            yesterday,
        )
        conn.close()

        code = main([
            "ledger", "--db", db_path,
            "--prefer-mock", "--email-preview",
        ])
    assert code == 0
    assert _FakeSmtp.sent
    body = _FakeSmtp.sent[0].get_content()
    assert "Yesterday's Results" in body
    assert "Daily Edge:" in body
    assert "Season Ledger:" in body        # all-time footer still present
