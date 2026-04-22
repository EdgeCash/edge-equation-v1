"""
AI-graphic prompt builder.

The prompt is a copy-paste-ready block for DALL-E / Midjourney / etc.
It is deterministic (same card -> same prompt), never shows units, and
pins EV Simulations to 10,000 per brand spec.
"""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.posting.ai_graphic_prompt import (
    ALGORITHM_VERSION,
    EngineStats,
    _grade_score,
    build_ai_graphic_prompt,
    stable_engine_stats,
)
from edge_equation.posting.posting_formatter import PostingFormatter


def _pick(grade="A+", edge="0.09", sport="MLB", market="ML",
          selection="Home", game_id="G1", odds=-115,
          home="NYY", away="BOS"):
    return Pick(
        sport=sport, market_type=market, selection=selection,
        line=Line(odds=odds),
        fair_prob=Decimal("0.55"),
        edge=Decimal(edge),
        kelly=Decimal("0.02"),
        grade=grade,
        game_id=game_id,
        metadata={"home_team": home, "away_team": away},
    )


# ---------------------------------------------------- engine stats

def test_stable_engine_stats_are_deterministic():
    a = stable_engine_stats("2026-04-22T09:00:00")
    b = stable_engine_stats("2026-04-22T09:00:00")
    assert a == b


def test_stable_engine_stats_vary_by_seed():
    a = stable_engine_stats("2026-04-22T09:00:00")
    b = stable_engine_stats("2026-04-23T09:00:00")
    assert a != b


def test_ev_simulations_always_10000():
    for seed in ("a", "b", "c", "2026-04-22", ""):
        s = stable_engine_stats(seed)
        assert s.ev_simulations == 10_000


def test_engine_stats_in_plausible_ranges():
    s = stable_engine_stats("2026-04-22")
    assert 3.0 <= s.run_time_sec <= 8.0
    assert 12_000 <= s.data_points_scanned <= 28_000
    assert 300 <= s.correlation_models <= 500


# ---------------------------------------------------- grade score

def test_grade_score_bounds():
    assert _grade_score(None) == 85
    assert _grade_score(Decimal("0.00")) == 85
    assert _grade_score(Decimal("0.05")) == 90
    assert _grade_score(Decimal("0.09")) == 94
    assert _grade_score(Decimal("0.14")) == 99
    # Clamps at 99
    assert _grade_score(Decimal("0.30")) == 99


# ---------------------------------------------------- prompt content

def _daily_card_with(picks):
    card = PostingFormatter.build_card(
        card_type="daily_edge",
        picks=picks,
        generated_at="2026-04-22T09:00:00",
        skip_filter=True,
    )
    return card


def test_prompt_contains_three_tier_labels_when_all_grades_present():
    card = _daily_card_with([
        _pick(grade="A+", edge="0.09", game_id="G1"),
        _pick(grade="A",  edge="0.06", game_id="G2"),
        _pick(grade="B",  edge="0.04", game_id="G3"),
    ])
    text = build_ai_graphic_prompt(card)
    assert "A+ TIER" in text
    assert "A TIER" in text
    assert "A- TIER" in text   # brand maps engine-grade B -> A-
    assert "SIGMA PLAY" in text
    assert "PRECISION PLAY" in text
    assert "SHARP PLAY" in text


def test_prompt_never_renders_units_or_kelly_in_pick_rows():
    card = _daily_card_with([_pick(grade="A", edge="0.07")])
    text = build_ai_graphic_prompt(card)
    # Isolate just the pick-row lines (they start with four spaces and
    # contain "GRADE:"). The top-level prompt preamble tells the AI to
    # exclude units/Kelly/edge, which is correct -- we only care that
    # they don't leak INTO the card content itself.
    pick_rows = [ln for ln in text.splitlines() if "GRADE:" in ln]
    assert pick_rows, "expected at least one pick row"
    for row in pick_rows:
        assert "Kelly" not in row, f"Kelly leaked into pick row: {row!r}"
        assert "Edge" not in row, f"Edge leaked into pick row: {row!r}"
        assert "%" not in row, f"percentage leaked into pick row: {row!r}"
        assert "1u" not in row and "2u" not in row, f"unit leaked: {row!r}"


def test_prompt_pins_ev_simulations_to_10000():
    card = _daily_card_with([_pick()])
    text = build_ai_graphic_prompt(card)
    assert "EV Simulations ...... 10,000" in text


def test_prompt_engine_stats_deterministic_for_same_card():
    card = _daily_card_with([_pick()])
    a = build_ai_graphic_prompt(card)
    b = build_ai_graphic_prompt(card)
    assert a == b


def test_prompt_engine_stats_vary_by_date():
    card_a = _daily_card_with([_pick()])
    card_a["generated_at"] = "2026-04-22T09:00:00"
    card_b = _daily_card_with([_pick()])
    card_b["generated_at"] = "2026-05-15T09:00:00"
    assert build_ai_graphic_prompt(card_a) != build_ai_graphic_prompt(card_b)


def test_prompt_shows_grade_score_in_85_99_range():
    card = _daily_card_with([
        _pick(grade="A+", edge="0.09"),
        _pick(grade="A",  edge="0.06", game_id="G2"),
        _pick(grade="B",  edge="0.04", game_id="G3"),
    ])
    text = build_ai_graphic_prompt(card)
    # A+ @ 0.09 -> 94, A @ 0.06 -> 91, B @ 0.04 -> 89
    assert "GRADE: A+ (94)" in text
    assert "GRADE: A (91)" in text
    assert "GRADE: A- (89)" in text


def test_prompt_renders_matchup_and_odds():
    card = _daily_card_with([
        _pick(grade="A+", edge="0.09", home="NSH", away="MIN",
              selection="Kirill Kaprizov OVER 3.5 SOG", odds=-130),
    ])
    text = build_ai_graphic_prompt(card)
    assert "Kirill Kaprizov OVER 3.5 SOG (-130)" in text
    assert "MIN @ NSH" in text


def test_prompt_graceful_when_no_tiered_plays():
    # Evening stable / empty slate: still renders a valid prompt.
    card = PostingFormatter.build_card(
        card_type="evening_edge", picks=[],
        generated_at="2026-04-22T18:00:00",
    )
    text = build_ai_graphic_prompt(card)
    assert "No tiered plays" in text or "Engine stable" in text
    # Engine-data footer still present
    assert "Algorithm" in text
    assert "EV Simulations" in text


def test_prompt_header_carries_date_and_algo_version():
    card = _daily_card_with([_pick()])
    text = build_ai_graphic_prompt(card)
    assert ALGORITHM_VERSION in text
    assert "APRIL" in text.upper()


# ---------------------------------------------------- email integration

class _FakeSmtp:
    sent = []
    def __init__(self, host, port): self.host = host; self.port = port
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
    for v in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
              "X_ACCESS_TOKEN_SECRET", "DISCORD_WEBHOOK_URL",
              "THE_ODDS_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_daily_email_preview_body_carries_both_sections(tmp_path, monkeypatch):
    from edge_equation.__main__ import main
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "prev.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        code = main([
            "daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--email-preview",
        ])
    assert code == 0
    assert len(_FakeSmtp.sent) == 1
    body = _FakeSmtp.sent[0].get_content()
    assert "TEXT OF WHAT WOULD POST TO X" in body
    assert "AI GRAPHIC PROMPT" in body
    assert "SIGMA PLAY" in body or "PRECISION PLAY" in body
    assert "EV Simulations" in body
    # No unit markers anywhere in the email
    assert "1u" not in body


def test_ledger_email_preview_has_no_ai_prompt(tmp_path, monkeypatch):
    from edge_equation.__main__ import main
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "prev2.db")
    with patch("edge_equation.publishing.email_publisher.smtplib.SMTP", _FakeSmtp):
        code = main([
            "ledger", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--email-preview",
        ])
    assert code == 0
    body = _FakeSmtp.sent[0].get_content()
    # Ledger email stays X-text-only; no AI-prompt section.
    assert "AI GRAPHIC PROMPT" not in body
