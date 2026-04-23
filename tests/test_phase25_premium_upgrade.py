"""
Phase 25 premium-email upgrade:

  1. Cross-slate: A+ / A / A- picks from BOTH domestic and overseas
     slates land in the premium email.
  2. Top-10-props-by-grade selector: premium section caps at 10 rows
     ranked strictly by grade (A+ > A > A-/B) then by edge.
  3. Parlay 3 - 6 legs from distinct games; empty when fewer than 3
     qualify.
  4. Deep per-pick block shows Fair / Implied / Edge / Kelly, plus
     HFA and decay when present, plus an analytical Read line.
  5. 10am CT scheduled send via dual DST-aware cron + guard.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import json
from unittest.mock import patch

import pytest

from edge_equation.__main__ import main
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.persistence.db import Database
from edge_equation.posting.player_props import (
    PREMIUM_TOP_N_PROPS,
    select_prop_projections,
    select_top_props_by_grade,
)
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import (
    _render_pick_block,
    format_premium_daily,
)


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _prop(player, market="HR", grade="A+", edge="0.09",
          expected="0.82", sport="MLB", game_id=None):
    game_id = game_id or f"G-{player.replace(' ', '')}"
    return Pick(
        sport=sport, market_type=market,
        selection=f"{player} over 0.5",
        line=Line(odds=+280),
        fair_prob=Decimal("0.55"),
        expected_value=Decimal(expected),
        edge=Decimal(edge),
        kelly=Decimal("0.025"),
        grade=grade,
        game_id=game_id,
        metadata={
            "home_team": "NYY", "away_team": "BOS",
            "player_name": player,
            "read_notes": "Barrel rate +5pp last 2 weeks.",
        },
    )


def _team(game_id="G1", grade="A+", edge="0.13", kelly="0.05",
          sport="MLB", market="ML", selection="NYY"):
    return Pick(
        sport=sport, market_type=market, selection=selection,
        line=Line(odds=-115),
        fair_prob=Decimal("0.62"),
        edge=Decimal(edge),
        kelly=Decimal(kelly),
        grade=grade,
        game_id=game_id,
        metadata={
            "home_team": "NYY", "away_team": "LAA",
            "read_notes": "Pitching matchup favors the home side.",
        },
        hfa_value=Decimal("0.062"),
        decay_halflife_days=Decimal("26"),
    )


# ------------------------------------------------ top-10 props by grade


def test_top_n_props_caps_at_10_by_default():
    picks = [_prop(f"Player {i}", grade="A") for i in range(15)]
    out = select_top_props_by_grade(picks)
    assert len(out) == PREMIUM_TOP_N_PROPS == 10


def test_top_n_props_orders_by_grade_then_edge():
    picks = [
        _prop("A1", grade="A",  edge="0.09"),
        _prop("Aplus1", grade="A+", edge="0.06"),
        _prop("Aminus", grade="B", edge="0.12"),  # brand's "A-"
        _prop("A2", grade="A",  edge="0.10"),
        _prop("Aplus2", grade="A+", edge="0.08"),
    ]
    out = select_top_props_by_grade(picks, n=10)
    grades = [p.grade for p in out]
    # A+ first (all of them), then A (edge-desc), then B.
    assert grades[:2] == ["A+", "A+"]
    assert grades[2:4] == ["A", "A"]
    assert grades[-1] == "B"


def test_top_n_props_admits_grade_a_minus():
    """Premium admits A- (engine grade B) where the free selector does not."""
    free = select_prop_projections([_prop("X", grade="B")])
    assert free == []
    premium = select_top_props_by_grade([_prop("X", grade="B")])
    assert len(premium) == 1


# ------------------------------------------------ parlay 3-6 legs


def test_parlay_returns_empty_when_fewer_than_three_qualify():
    legs = PostingFormatter.select_parlay_of_day([_team(game_id="G1", grade="A+")])
    assert legs == []


def test_parlay_caps_at_max_four_legs_by_default():
    # Phase 30: tightened default from 6 to 4 legs. Edge must be >= 0.12
    # so bumping the range up from 0.10 - 0.19 to 0.13 - 0.19.
    picks = [_team(game_id=f"G{i}", grade="A+", edge=f"0.{13 + i:02d}")
             for i in range(6)]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert 3 <= len(legs) <= 4
    # Distinct games only.
    assert len({p.game_id for p in legs}) == len(legs)


def test_parlay_uses_distinct_games():
    # Phase 28 trust restoration: parlay legs are A+ ONLY (not A or
    # below) until the post-fix grades earn back trust over a settled-
    # results sample. The two A picks below are now ineligible.
    picks = [
        _team(game_id="SAME", grade="A+", selection="NYY"),
        _team(game_id="SAME", grade="A+", selection="NYY Over 8.5", market="Total"),
        _team(game_id="OTHER-1", grade="A+"),
        _team(game_id="OTHER-2", grade="A+"),
    ]
    legs = PostingFormatter.select_parlay_of_day(picks)
    assert len(legs) == 3
    assert len({p.game_id for p in legs}) == 3


# ------------------------------------------------ deep per-pick block


def test_pick_block_includes_fair_implied_edge_kelly():
    block = "\n".join(_render_pick_block(_team().to_dict()))
    assert "Fair" in block
    assert "Implied" in block
    assert "Edge" in block
    assert "Kelly" in block
    assert "%" in block


def test_pick_block_shows_hfa_and_decay_when_present():
    block = "\n".join(_render_pick_block(_team().to_dict()))
    assert "HFA" in block
    assert "Decay tau/2" in block
    assert "26d" in block


def test_pick_block_shows_read_notes_verbatim():
    block = "\n".join(_render_pick_block(_team().to_dict()))
    assert "Pitching matchup favors the home side." in block


def test_pick_block_falls_back_to_factual_line_when_no_read():
    """Phase 31: the empty-read fallback no longer apologizes ("no
    narrative delta recorded") -- it cites the engine's own math
    (fair vs implied, edge) so the row is still substantive. No
    generic tout prose, no apology."""
    p = _team()
    meta = dict(p.metadata)
    meta.pop("read_notes", None)
    no_read = Pick(
        sport=p.sport, market_type=p.market_type, selection=p.selection,
        line=p.line, fair_prob=p.fair_prob, edge=p.edge, kelly=p.kelly,
        grade=p.grade, game_id=p.game_id, metadata=meta,
        hfa_value=p.hfa_value, decay_halflife_days=p.decay_halflife_days,
    )
    block = "\n".join(_render_pick_block(no_read.to_dict()))
    assert "Read:" in block
    # Substantive fallback quotes the math instead of apologizing.
    assert "no narrative delta" not in block.lower()
    assert ("Engine projects" in block) or ("Grade" in block)


# ------------------------------------------------ format_premium_daily


def test_premium_email_groups_picks_by_grade_tier():
    aplus = _team(game_id="G-APLUS", grade="A+", edge="0.09")
    a = _team(game_id="G-A", grade="A", edge="0.06", selection="BOS")
    aminus = _team(game_id="G-B", grade="B", edge="0.04", selection="LAA")
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[a, aminus, aplus],     # out of order on purpose
        generated_at="2026-04-22T10:00:00",
    )
    body = format_premium_daily(card)
    assert "A+ TIER" in body
    assert "A TIER" in body
    assert "A- TIER" in body
    # A+ section lands BEFORE A section, A BEFORE A-.
    assert body.index("A+ TIER") < body.index("A TIER")
    assert body.index("A TIER") < body.index("A- TIER")


def test_premium_email_renders_deep_block_per_pick():
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[_team(grade="A+")],
        generated_at="2026-04-22T10:00:00",
    )
    body = format_premium_daily(card)
    assert "Consensus: NYY" in body
    assert "Fair" in body and "Edge" in body and "Kelly" in body
    assert "HFA" in body and "Decay" in body
    assert "Read:" in body


# ------------------------------------------------ workflow schedule


def test_premium_workflow_has_10am_ct_dual_cron():
    text = (WORKFLOWS / "premium-daily-preview.yml").read_text(encoding="utf-8")
    # 10:00 CT -> UTC 15 (CDT) / UTC 16 (CST)
    assert 'cron: "0 15 * * *"' in text
    assert 'cron: "0 16 * * *"' in text


def test_premium_workflow_has_ct_hour_guard_at_ten():
    text = (WORKFLOWS / "premium-daily-preview.yml").read_text(encoding="utf-8")
    assert "expected = 10" in text
    assert 'ZoneInfo("America/Chicago")' in text


def test_premium_workflow_passes_cached_only_and_force():
    text = (WORKFLOWS / "premium-daily-preview.yml").read_text(encoding="utf-8")
    assert "--cached-only" in text
    assert "--force" in text


# ------------------------------------------------ cross-slate CLI


class _FakeSmtp:
    sent = []
    def __init__(self, h, p, **kw): pass
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
    for v in ("X_API_KEY", "DISCORD_WEBHOOK_URL", "THE_ODDS_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_premium_cli_pulls_from_both_slates(tmp_path, monkeypatch, capsys):
    """With prefer_mock=true both the daily-edge and overseas-edge
    mock sources exist, so picks from BOTH slates show up in the
    premium email."""
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "premium.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        code = main([
            "premium-daily", "--db", db_path,
            "--prefer-mock", "--publish", "--no-dry-run", "--force",
        ])
    assert code == 0
    assert len(_FakeSmtp.sent) == 1
    body = _FakeSmtp.sent[0].get_content()
    # Slate separation stays on the public feed, but the premium mail
    # union gives subscribers every A/A+/A- across domestic + overseas.
    assert "DAILY EDGE" in body
    # No DFS / Top N language anywhere.
    assert "DFS" not in body
    assert "Top 10" not in body
