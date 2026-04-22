"""
Premium Daily card: A+/A/A- admission, parlay-of-day, top-6 props,
yesterday's engine hit rate, and the plain-text email body renderer.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from edge_equation.__main__ import build_parser, main
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.engine.realization import RealizationTracker
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import format_premium_daily


def _pick(
    *, grade="A+", edge="0.10", sport="MLB", market="ML",
    selection="Home", game_id="G1", odds=-110, number=None,
):
    return Pick(
        sport=sport,
        market_type=market,
        selection=selection,
        line=Line(odds=odds, number=Decimal(str(number)) if number is not None else None),
        fair_prob=Decimal("0.55"),
        edge=Decimal(str(edge)),
        kelly=Decimal("0.02"),
        grade=grade,
        game_id=game_id,
        metadata={"home_team": "NYY", "away_team": "BOS"},
    )


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ------------------------------------------- filters & selectors

def test_filter_premium_daily_admits_a_plus_a_and_b():
    picks = [
        _pick(grade="A+", edge="0.12"),
        _pick(grade="A", edge="0.07", game_id="G2"),
        _pick(grade="B", edge="0.035", game_id="G3"),
        _pick(grade="C", edge="0.02", game_id="G4"),
        _pick(grade="D", edge="-0.01", game_id="G5"),
    ]
    out = PostingFormatter.filter_premium_daily(picks)
    assert [p.grade for p in out] == ["A+", "A", "B"]


def test_filter_premium_daily_sorts_grade_then_edge():
    a_low = _pick(grade="A", edge="0.05", game_id="G-LOW")
    a_high = _pick(grade="A", edge="0.09", game_id="G-HIGH")
    out = PostingFormatter.filter_premium_daily([a_low, a_high])
    assert out[0].game_id == "G-HIGH"
    assert out[1].game_id == "G-LOW"


def test_select_parlay_of_day_uses_distinct_games():
    # Phase 28 trust restoration: parlays admit A+ only. Fixture
    # picks default to A+ here so the grade gate passes.
    g1a = _pick(game_id="G1", market="ML")
    g1b = _pick(game_id="G1", market="Total", selection="Over 9")
    g2 = _pick(game_id="G2", edge="0.08")
    g3 = _pick(game_id="G3", edge="0.07")
    legs = PostingFormatter.select_parlay_of_day([g1a, g1b, g2, g3])
    assert len(legs) == 3
    assert len({l.game_id for l in legs}) == 3


def test_select_parlay_returns_empty_below_min_legs():
    # Phase 25: a parlay needs 3 legs minimum. One qualifying pick is
    # not a parlay -- the premium email renders the empty-state line
    # instead of shipping a nonsensical 1-leg "parlay".
    legs = PostingFormatter.select_parlay_of_day([_pick(game_id="ONLY-ONE")])
    assert legs == []


def test_select_parlay_honors_max_legs_ceiling():
    # Default max_legs=6 so a huge input still caps at 6 distinct games.
    picks = [_pick(game_id=f"G{i}") for i in range(10)]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert 3 <= len(legs) <= 6


def test_select_top_props_only_prop_markets():
    ml = _pick(market="ML", game_id="G1")
    total = _pick(market="Total", game_id="G2", selection="Over 9")
    hr = _pick(market="HR", game_id="G3", edge="0.09")
    k = _pick(market="K", game_id="G4", edge="0.07")
    top = PostingFormatter.select_top_props([ml, total, hr, k])
    assert {p.market_type for p in top} == {"HR", "K"}
    assert top[0].market_type == "HR"  # higher edge


def test_select_top_props_caps_at_n():
    props = [
        _pick(market="HR", game_id=f"G{i}", edge=f"0.{10+i:02d}")
        for i in range(10)
    ]
    top = PostingFormatter.select_top_props(props, n=6)
    assert len(top) == 6


# ------------------------------------------- build_card integration

def test_build_card_premium_daily_emits_parlay_and_top_props():
    # Phase 28: parlay needs >=3 distinct A+ legs (each with edge in
    # the trustworthy range). Bumping all three to A+ since the
    # parlay-of-the-day rule no longer admits A or A-/B legs.
    picks = [
        _pick(grade="A+", market="ML", game_id="G1", edge="0.12"),
        _pick(grade="A+", market="HR", game_id="G2", edge="0.09"),
        _pick(grade="A+", market="K", game_id="G3", edge="0.05"),
    ]
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=picks,
        engine_health={"wins": 5, "losses": 3, "pushes": 0, "n": 8, "hit_rate": 0.625},
    )
    assert card["card_type"] == "premium_daily"
    assert len(card["picks"]) == 3
    assert card["parlay"]
    assert card["top_props"]
    assert card["engine_health"]["hit_rate"] == 0.625
    # Edge + Kelly survive (not public_mode)
    assert card["picks"][0]["edge"]
    assert card["picks"][0]["kelly"]


def test_build_card_premium_daily_filters_c_and_below():
    picks = [_pick(grade="C", game_id="C-GAME")]
    card = PostingFormatter.build_card(card_type="premium_daily", picks=picks)
    assert card["picks"] == []


# ------------------------------------------- engine health helper

def test_hit_rate_since_computes_yesterday(conn):
    yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
    old_date = (datetime.utcnow() - timedelta(days=7)).date().isoformat()
    slate = SlateRecord(
        slate_id=f"daily_edge_{yesterday.replace('-', '')}",
        generated_at=yesterday, sport=None, card_type="daily_edge", metadata={},
    )
    SlateStore.insert(conn, slate)
    won = _pick(grade="A")
    lost = _pick(grade="A", game_id="G-LOSE")
    PickStore.insert_many(conn, [won, lost],
                          slate_id=slate.slate_id, recorded_at=yesterday)
    # Mark realizations manually (100 = win, 0 = loss)
    conn.execute("UPDATE picks SET realization = 100 WHERE game_id = 'G1'")
    conn.execute("UPDATE picks SET realization = 0 WHERE game_id = 'G-LOSE'")
    conn.commit()
    # Old slate whose picks shouldn't be counted
    SlateStore.insert(conn, SlateRecord(
        slate_id=f"daily_edge_{old_date.replace('-', '')}",
        generated_at=old_date, sport=None, card_type="daily_edge", metadata={},
    ))
    PickStore.insert_many(conn, [_pick(game_id="G-OLD")],
                          slate_id=f"daily_edge_{old_date.replace('-', '')}",
                          recorded_at=old_date)
    conn.execute("UPDATE picks SET realization = 100 WHERE game_id = 'G-OLD'")
    conn.commit()

    health = RealizationTracker.hit_rate_since(conn, since_iso=yesterday)
    assert health["wins"] == 1
    assert health["losses"] == 1
    assert health["n"] == 2
    assert abs(health["hit_rate"] - 0.5) < 1e-6


# ------------------------------------------- renderer

def test_format_premium_daily_includes_all_sections():
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[
            _pick(grade="A+", market="ML", game_id="G1"),
            _pick(grade="A", market="HR", selection="Judge over 0.5",
                  odds=+280, game_id="G2"),
        ],
        engine_health={"wins": 3, "losses": 2, "pushes": 1, "n": 6, "hit_rate": 0.6},
        generated_at="2026-04-22T11:00:00",
    )
    body = format_premium_daily(card)
    # Phase 24 renamed "FULL SLATE" -> "DAILY EDGE", replaced the
    # DFS/TOP PROPS block with "PLAYER PROP PROJECTIONS", and added
    # YESTERDAY'S LEDGER + SPOTLIGHT sections.
    assert "PREMIUM DAILY EDGE" in body
    assert "2026-04-22" in body
    assert "DAILY EDGE" in body
    assert "SPOTLIGHT" in body
    assert "PARLAY OF THE DAY" in body
    assert "ENGINE HEALTH" in body
    assert "Hit rate:  60.0%" in body


def test_format_premium_daily_empty_slate_still_renders():
    card = PostingFormatter.build_card(card_type="premium_daily", picks=[])
    body = format_premium_daily(card)
    # Every always-rendered section still appears -- empty states
    # included. Player Prop Projections is OPT-IN: if no props
    # qualify, the section is omitted rather than showing a
    # near-empty table. That's part of the "no forcing content" rule.
    assert "DAILY EDGE" in body
    assert "SPOTLIGHT" in body
    assert "no qualifying picks" in body
    assert "no parlay legs" in body


# ------------------------------------------- CLI integration

class _FakeSmtp:
    sent = []
    def __init__(self, host, port, **kwargs): self.host, self.port = host, port
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): pass
    def send_message(self, msg): type(self).sent.append(msg)


def _isolate(monkeypatch, tmp_path):
    _FakeSmtp.sent = []
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "bot@edge.com")
    monkeypatch.setenv("EMAIL_TO", "ProfessorEdgeCash@gmail.com")
    for v in ("X_API_KEY", "X_API_SECRET", "THE_ODDS_API_KEY",
              "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(v, raising=False)


def test_premium_daily_subcommand_registered():
    parser = build_parser()
    args = parser.parse_args(["premium-daily"])
    assert args.subcommand == "premium-daily"


def test_premium_daily_cli_emails_to_professor_default(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "premium.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        code = main([
            "premium-daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--publish", "--no-dry-run",
        ])
    cap = capsys.readouterr()
    assert code == 0, cap.err
    assert len(_FakeSmtp.sent) == 1
    msg = _FakeSmtp.sent[0]
    assert msg["To"] == "ProfessorEdgeCash@gmail.com"
    assert "[Premium]" in msg["Subject"]
    body = msg.get_content()
    assert "PREMIUM DAILY EDGE" in body
    assert "DAILY EDGE" in body
    assert "ENGINE HEALTH" in body
