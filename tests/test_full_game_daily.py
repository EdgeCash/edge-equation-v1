"""Tests for the full-game daily orchestrator + email-block renderer.

Mirrors `test_props_daily.py`. The Odds API client is mocked via
dependency injection, and the projection rates table is overridden
via `rates_by_team`, so the suite runs without network or duckdb.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from edge_equation.engines.full_game import (
    FullGameEdgePick,
    FullGameLine,
    FullGameOutput,
    MLB_FULL_GAME_MARKETS,
    TeamRollingRates,
    build_full_game_output,
)
from edge_equation.engines.full_game import daily as daily_mod
from edge_equation.engines.tiering import Tier, TierClassification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_full_game_output(*, tier=Tier.STRONG, edge_pp=7.4,
                                model_prob=0.58, market_prob=0.50,
                                american_odds=-110, market="Total",
                                side="Over", line_value=8.5,
                                team_tricode=""):
    m = MLB_FULL_GAME_MARKETS[market]
    clf = TierClassification(tier=tier, basis="edge",
                                value=edge_pp / 100.0,
                                band_lower=0.05, band_upper=0.08)
    pick = FullGameEdgePick(
        market_canonical=m.canonical, market_label=m.label,
        home_team="New York Yankees", away_team="Boston Red Sox",
        home_tricode="NYY", away_tricode="BOS",
        side=side, team_tricode=team_tricode, line_value=line_value,
        model_prob=model_prob, market_prob_raw=0.55,
        market_prob_devigged=market_prob, vig_corrected=False,
        edge_pp=edge_pp, american_odds=american_odds,
        decimal_odds=1.91 if american_odds < 0 else 2.5,
        book="draftkings", tier=tier, tier_classification=clf,
    )
    return build_full_game_output(
        pick, confidence=0.72, lam_used=9.54,
        lam_home=4.57, lam_away=4.97,
        blend_n_home=40, blend_n_away=40,
    )


def _line(canonical="Total", side="Over", line_value=8.5,
            american_odds=-110, home_tri="NYY", away_tri="BOS",
            team_tricode=""):
    m = MLB_FULL_GAME_MARKETS[canonical]
    return FullGameLine(
        event_id="evt1", home_team="New York Yankees",
        away_team="Boston Red Sox",
        home_tricode=home_tri, away_tricode=away_tri,
        commence_time="2026-04-29T23:05:00Z", market=m,
        side=side, line_value=line_value,
        american_odds=float(american_odds),
        decimal_odds=1.91 if american_odds < 0 else 2.5,
        book="draftkings", team_tricode=team_tricode,
    )


# ---------------------------------------------------------------------------
# render_top_full_game_block
# ---------------------------------------------------------------------------


def test_render_block_empty_returns_empty_string():
    assert daily_mod.render_top_full_game_block([]) == ""


def test_render_block_includes_header_and_separator():
    text = daily_mod.render_top_full_game_block([_build_full_game_output()])
    assert "FULL-GAME BOARD — Top 1 by Edge" in text
    assert "═" * 60 in text


def test_render_block_caps_at_n():
    outputs = [_build_full_game_output() for _ in range(20)]
    text = daily_mod.render_top_full_game_block(outputs, n=5)
    for i in range(1, 6):
        assert f"{i:>2}.  " in text
    assert " 6.  " not in text


def test_render_block_indents_continuation_lines():
    text = daily_mod.render_top_full_game_block(
        [_build_full_game_output()], n=1,
    )
    lines = text.splitlines()
    rank_idx = next(i for i, l in enumerate(lines) if l.startswith(" 1.  "))
    # Continuation must be indented by 5 spaces (matches " 1.  " prefix).
    assert lines[rank_idx + 1].startswith("     ")


# ---------------------------------------------------------------------------
# build_full_game_card — fallbacks
# ---------------------------------------------------------------------------


def test_build_card_no_lines_returns_empty_card(monkeypatch):
    monkeypatch.setattr(daily_mod, "fetch_all_full_game_lines",
                          lambda **kw: [])
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    card = daily_mod.build_full_game_card(
        "2026-04-29", persist=False, settle_yesterday=False,
    )
    assert card.target_date == "2026-04-29"
    assert card.n_lines_fetched == 0
    assert card.n_qualifying_picks == 0
    assert card.top_board_text == ""


def test_build_card_swallows_fetch_errors(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("Odds API rate-limited")
    monkeypatch.setattr(daily_mod, "fetch_all_full_game_lines", _boom)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    card = daily_mod.build_full_game_card(
        "2026-04-29", persist=False, settle_yesterday=False,
    )
    assert card.n_lines_fetched == 0
    assert card.picks == []


# ---------------------------------------------------------------------------
# build_full_game_card — happy path with mocked lines
# ---------------------------------------------------------------------------


def test_build_card_happy_path_renders_top_board(monkeypatch):
    """Two Over/Under lines on a high-scoring matchup produce a STRONG
    Over edge → the orchestrator surfaces it on the top board."""
    lines = [
        _line(side="Over",  line_value=8.5, american_odds=+150),  # implied 40%
        _line(side="Under", line_value=8.5, american_odds=-180),
    ]
    monkeypatch.setattr(daily_mod, "fetch_all_full_game_lines",
                          lambda **kw: lines)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    monkeypatch.setattr(daily_mod, "_safe_settle",
                          lambda cfg, dt: None)
    monkeypatch.setattr(daily_mod, "_persist_predictions",
                          lambda cfg, outs, dt: None)

    # High-scoring matchup → λ_total ~10 → P(Over 8.5) > 60%.
    rates = {
        "NYY": TeamRollingRates(team_tricode="NYY", n_games=40,
                                  end_date="2026-04-28", lookback_days=45,
                                  runs_per_game=5.5, runs_allowed_per_game=4.5),
        "BOS": TeamRollingRates(team_tricode="BOS", n_games=40,
                                  end_date="2026-04-28", lookback_days=45,
                                  runs_per_game=5.5, runs_allowed_per_game=4.5),
    }
    card = daily_mod.build_full_game_card(
        "2026-04-29", rates_by_team=rates,
        persist=False, settle_yesterday=False,
    )
    assert card.n_lines_fetched == 2
    assert card.n_qualifying_picks >= 1
    assert "FULL-GAME BOARD" in card.top_board_text


def test_build_card_persists_when_persist_true(monkeypatch):
    lines = [
        _line(side="Over", line_value=8.5, american_odds=+150),
        _line(side="Under", line_value=8.5, american_odds=-180),
    ]
    monkeypatch.setattr(daily_mod, "fetch_all_full_game_lines",
                          lambda **kw: lines)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    monkeypatch.setattr(daily_mod, "_safe_settle",
                          lambda cfg, dt: None)

    persisted: list[tuple] = []

    def _record_persist(cfg, outs, dt):
        persisted.append((dt, list(outs)))

    monkeypatch.setattr(daily_mod, "_persist_predictions", _record_persist)

    rates = {
        "NYY": TeamRollingRates(team_tricode="NYY", n_games=40,
                                  end_date="2026-04-28", lookback_days=45,
                                  runs_per_game=5.5, runs_allowed_per_game=4.5),
        "BOS": TeamRollingRates(team_tricode="BOS", n_games=40,
                                  end_date="2026-04-28", lookback_days=45,
                                  runs_per_game=5.5, runs_allowed_per_game=4.5),
    }
    daily_mod.build_full_game_card(
        "2026-04-29", rates_by_team=rates, persist=True,
        settle_yesterday=False,
    )
    assert len(persisted) == 1
    assert persisted[0][0] == "2026-04-29"


# ---------------------------------------------------------------------------
# Email integration — render_body picks up FG block when present
# ---------------------------------------------------------------------------


def _minimal_card(**overrides):
    base = {
        "headline": "NRFI Daily — 2026-04-29",
        "subhead": "Facts. Not Feelings.",
        "tagline": "(footer)",
        "engine": "ml",
        "target_date": "2026-04-29",
        "picks": [],
        "ledger_text": "",
        "parlay_text": "",
        "parlay_ledger_text": "",
        "props_top_text": "",
        "props_ledger_text": "",
        "fullgame_top_text": "",
        "fullgame_ledger_text": "",
    }
    base.update(overrides)
    return base


def test_render_body_includes_fullgame_block_when_present():
    from edge_equation.engines.nrfi import email_report as er
    fg_block = (
        "FULL-GAME BOARD — Top 2 by Edge\n"
        + "═" * 60 + "\n"
        + " 1.  BOS @ NYY · Total Runs Over 8.5"
    )
    body = er.render_body(_minimal_card(fullgame_top_text=fg_block))
    assert "FULL-GAME BOARD" in body
    assert "Total Runs Over 8.5" in body


def test_render_body_includes_fullgame_ledger_when_present():
    from edge_equation.engines.nrfi import email_report as er
    fg_ledger = (
        "FULL-GAME YTD LEDGER (2026)\n"
        + "─" * 60 + "\n"
        + "  Total       LOCK   3-1  +1.50u"
    )
    body = er.render_body(_minimal_card(fullgame_ledger_text=fg_ledger))
    assert "FULL-GAME YTD LEDGER" in body
    assert "+1.50u" in body


def test_render_body_skips_fullgame_when_empty():
    from edge_equation.engines.nrfi import email_report as er
    body = er.render_body(_minimal_card())
    assert "FULL-GAME BOARD" not in body
    assert "FULL-GAME YTD LEDGER" not in body


def test_render_body_orders_props_before_fullgame():
    """The email reads top → bottom: NRFI → parlays → props → full-game.
    Verify props block lands above the full-game block when both
    are present."""
    from edge_equation.engines.nrfi import email_report as er
    props_block = "PROPS BOARD — Top 2 by Edge\n" + "═" * 60
    fg_block = "FULL-GAME BOARD — Top 2 by Edge\n" + "═" * 60
    body = er.render_body(_minimal_card(
        props_top_text=props_block, fullgame_top_text=fg_block,
    ))
    assert body.index("PROPS BOARD") < body.index("FULL-GAME BOARD")
