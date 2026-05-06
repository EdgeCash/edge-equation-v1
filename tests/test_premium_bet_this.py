"""Tests for the premium 'Bet This' combined exporter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.exporters.premium.bet_this import (
    MIN_PREMIUM_CONVICTION, MIN_PREMIUM_EDGE_PCT, MIN_PREMIUM_KELLY_PCT,
    PremiumPick, _bet_this_sentence, filter_premium,
    load_mlb_picks, load_wnba_picks, write_outputs,
)


def _mk_pick(**overrides) -> PremiumPick:
    base = dict(
        sport="MLB", matchup="AAA@BBB", market="Run Line",
        side="AAA +1.5", line="+1.5",
        conviction_pct=65.0, edge_pct=5.0, kelly_pct=2.5, kelly_advice="2u",
        market_odds_american=-115, book="fanduel",
    )
    base.update(overrides)
    return PremiumPick(**base)


def test_filter_drops_low_conviction():
    keep = _mk_pick(conviction_pct=70.0)
    drop = _mk_pick(conviction_pct=55.0)  # below default 60
    out = filter_premium([keep, drop])
    assert keep in out and drop not in out


def test_filter_drops_low_edge():
    keep = _mk_pick(edge_pct=5.0)
    drop = _mk_pick(edge_pct=3.0)  # below default 4.5
    out = filter_premium([keep, drop])
    assert len(out) == 1


def test_filter_drops_kelly_pass():
    drop = _mk_pick(kelly_advice="PASS")
    assert filter_premium([drop]) == []


def test_filter_sorts_descending_by_kelly():
    a = _mk_pick(kelly_pct=2.0)
    b = _mk_pick(kelly_pct=5.0)
    c = _mk_pick(kelly_pct=3.5)
    sorted_picks = filter_premium([a, b, c])
    assert [p.kelly_pct for p in sorted_picks] == [5.0, 3.5, 2.0]


def test_bet_this_sentence_format():
    p = _mk_pick(matchup="NYY@BOS", side="OVER 8.5", line="8.5",
                 market_odds_american=-105, book="draftkings",
                 kelly_advice="3u")
    s = _bet_this_sentence(p)
    assert s.startswith("Bet This: NYY@BOS")
    assert "OVER 8.5" in s
    assert "@ -105" in s
    assert "(draftkings)" in s
    assert "3u" in s


def test_load_mlb_picks_filters_status_play(tmp_path: Path):
    payload = {
        "tabs": {
            "moneyline": {
                "projections": [
                    {"away": "AAA", "home": "BBB", "ml_pick": "AAA",
                     "status": "PLAY", "conviction_pct": 62.5, "edge_pct": 5.0,
                     "kelly_pct": 2.5, "kelly_advice": "2u",
                     "market_odds_american": -110, "book": "fanduel"},
                    {"away": "CCC", "home": "DDD", "ml_pick": "CCC",
                     "status": "PASS", "conviction_pct": 51.0, "edge_pct": 0.5,
                     "kelly_pct": 0.0, "kelly_advice": "PASS"},
                ],
            },
        },
    }
    p = tmp_path / "mlb_daily.json"
    p.write_text(json.dumps(payload))
    picks = load_mlb_picks(p)
    assert len(picks) == 1
    assert picks[0].matchup == "AAA@BBB"
    assert picks[0].sport == "MLB"


def test_load_mlb_picks_handles_missing_file(tmp_path: Path):
    assert load_mlb_picks(tmp_path / "nope.json") == []


def test_load_wnba_picks_consumes_todays_card(tmp_path: Path):
    payload = {
        "todays_card": [
            {"matchup": "NYL@LV", "bet_type": "moneyline",
             "pick": "LV", "model_prob": 0.68, "edge_pct": 6.2,
             "kelly_pct": 3.0, "kelly_advice": "3u",
             "american_odds": -125, "book": "draftkings"},
        ],
    }
    p = tmp_path / "wnba_daily.json"
    p.write_text(json.dumps(payload))
    picks = load_wnba_picks(p)
    assert len(picks) == 1
    assert picks[0].sport == "WNBA"
    assert picks[0].conviction_pct == pytest.approx(68.0)


def test_write_outputs_creates_json_and_csv(tmp_path: Path):
    picks = filter_premium([_mk_pick()])
    written = write_outputs(
        picks, tmp_path, target_date="2026-05-09",
        generated_at="2026-05-09T12:00:00+00:00",
        thresholds={
            "min_conviction": MIN_PREMIUM_CONVICTION,
            "min_edge_pct":   MIN_PREMIUM_EDGE_PCT,
            "min_kelly_pct":  MIN_PREMIUM_KELLY_PCT,
        },
    )
    assert "json" in written and "csv" in written
    payload = json.loads(written["json"].read_text())
    assert payload["n_picks"] == 1
    assert payload["picks"][0]["sport"] == "MLB"
    csv_text = written["csv"].read_text()
    assert "sport" in csv_text.splitlines()[0]
