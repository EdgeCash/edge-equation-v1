"""Tests for the rolling-backtest gate wired into the props daily flow."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.exporters.props.daily import _resolve_market_gate


def test_resolve_market_gate_missing_file_is_cold_start(tmp_path: Path):
    p = tmp_path / "absent.json"
    passed, notes = _resolve_market_gate(p)
    assert passed is None
    assert notes == {}


def test_resolve_market_gate_malformed_file_is_cold_start(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("{not valid json")
    passed, notes = _resolve_market_gate(p)
    assert passed is None


def test_resolve_market_gate_passes_strong_markets(tmp_path: Path):
    """A market with 200+ bets, ROI > 1%, Brier < 0.246 must PASS."""
    p = tmp_path / "summary.json"
    p.write_text(json.dumps({
        "summary_by_bet_type_play_only": [
            {"bet_type": "K",   "bets": 14000, "roi_pct": 21.0, "brier": 0.20},
            {"bet_type": "RBI", "bets":  2000, "roi_pct": -5.0, "brier": 0.23},
            {"bet_type": "Hits","bets":   100, "roi_pct": 30.0, "brier": 0.20},
        ],
    }))
    passed, notes = _resolve_market_gate(p)
    assert passed == {"K"}
    assert "RBI" in notes  # negative ROI
    assert "Hits" in notes  # too few bets


def test_resolve_market_gate_prefers_play_only(tmp_path: Path):
    """When both summary slices are present, play_only wins."""
    p = tmp_path / "summary.json"
    p.write_text(json.dumps({
        "summary_by_bet_type": [
            {"bet_type": "K",   "bets": 50_000, "roi_pct": 0.5, "brier": 0.21},
        ],
        "summary_by_bet_type_play_only": [
            {"bet_type": "K",   "bets": 14_000, "roi_pct": 21.0, "brier": 0.20},
        ],
    }))
    passed, notes = _resolve_market_gate(p)
    # all-bets row would FAIL (ROI 0.5 < 1.0); play-only PASSES.
    assert passed == {"K"}


# ---------------------------------------------------------------------
# build_props_card honours the gate
# ---------------------------------------------------------------------

def test_build_props_card_drops_lines_outside_gate(monkeypatch):
    """When passed_markets is supplied, lines whose canonical market
    isn't in the set are dropped before projection."""
    from edge_equation.engines.props_prizepicks import daily as daily_mod
    from edge_equation.engines.props_prizepicks.markets import MLB_PROP_MARKETS
    from edge_equation.engines.props_prizepicks.odds_fetcher import (
        PlayerPropLine,
    )
    hr = MLB_PROP_MARKETS["HR"]
    rbi = MLB_PROP_MARKETS["RBI"]
    lines = [
        PlayerPropLine(
            event_id="e1", commence_time="2026-04-29T23:05:00Z",
            home_team="HOM", away_team="AWY",
            market=hr, player_name="P1", side="Over",
            line_value=0.5, american_odds=+250,
            decimal_odds=2.5, book="dk",
        ),
        PlayerPropLine(
            event_id="e1", commence_time="2026-04-29T23:05:00Z",
            home_team="HOM", away_team="AWY",
            market=rbi, player_name="P1", side="Over",
            line_value=0.5, american_odds=-110,
            decimal_odds=1.909, book="dk",
        ),
    ]
    monkeypatch.setattr(daily_mod, "fetch_all_player_props",
                        lambda **kw: lines)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                        lambda cfg, dt: "")
    monkeypatch.setattr(daily_mod, "_safe_settle",
                        lambda cfg, dt: None)
    monkeypatch.setattr(daily_mod, "_persist_predictions",
                        lambda cfg, outs, dt: None)

    # Cold-start: both lines fetched, both projected (no rates supplied
    # so the projector returns pure-prior LEAN+ rejected; check counts).
    cold = daily_mod.build_props_card(
        "2026-04-29", persist=False, settle_yesterday=False,
    )
    assert cold.n_lines_fetched == 2

    # Gate allows only HR -> 1 line survives the gate.
    gated = daily_mod.build_props_card(
        "2026-04-29", persist=False, settle_yesterday=False,
        passed_markets={"HR"},
    )
    assert gated.n_lines_fetched == 2  # fetched both
    assert gated.n_projected == 1     # but only 1 made it past the gate


def test_build_props_card_empty_after_gate_returns_clean_card(monkeypatch):
    """When the gate filters every line, the function still returns a
    well-formed PropsCard with zero counts -- no exceptions."""
    from edge_equation.engines.props_prizepicks import daily as daily_mod
    from edge_equation.engines.props_prizepicks.markets import MLB_PROP_MARKETS
    from edge_equation.engines.props_prizepicks.odds_fetcher import (
        PlayerPropLine,
    )
    hr = MLB_PROP_MARKETS["HR"]
    monkeypatch.setattr(daily_mod, "fetch_all_player_props",
                        lambda **kw: [
                            PlayerPropLine(
                                event_id="e1", commence_time="x",
                                home_team="H", away_team="A",
                                market=hr, player_name="P", side="Over",
                                line_value=0.5, american_odds=-110,
                                decimal_odds=1.909, book="dk",
                            ),
                        ])
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                        lambda cfg, dt: "")
    monkeypatch.setattr(daily_mod, "_safe_settle",
                        lambda cfg, dt: None)
    monkeypatch.setattr(daily_mod, "_persist_predictions",
                        lambda cfg, outs, dt: None)
    card = daily_mod.build_props_card(
        "2026-04-29", persist=False, settle_yesterday=False,
        passed_markets=set(),  # empty -> drops everything
    )
    assert card.n_qualifying_picks == 0
    assert card.picks == []
