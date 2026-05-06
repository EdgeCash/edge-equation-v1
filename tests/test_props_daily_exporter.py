"""Tests for the props daily exporter + Premium props loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_equation.engines.tiering import Tier
from edge_equation.exporters.premium.bet_this import (
    filter_premium, load_props_picks,
)
from edge_equation.exporters.props.daily import (
    CSV_COLUMNS, _premium_filter, write_outputs,
)


def _row(**overrides):
    base = dict(
        market_type="HR", market_label="Home Runs",
        player_name="Aaron Judge", line_value=0.5, side="Over",
        model_prob=0.62, model_pct=62.0, market_prob=0.45,
        market_prob_raw=0.46, vig_corrected=True, edge_pp=8.0,
        tier="STRONG", grade="B+", color_band="Green", color_hex="#0f0",
        kelly_units=1.5, american_odds=-120, decimal_odds=1.833,
        book="fanduel", lam=0.78, blend_n=210, confidence=0.62,
        mc_low=0.55, mc_high=0.70, mc_band_pp=15.0,
    )
    base.update(overrides)
    return base


def test_premium_filter_drops_below_tier():
    rows = [_row(tier="STRONG"), _row(tier="LEAN")]
    out = _premium_filter(rows, Tier.STRONG, 0.0, 0.0)
    assert len(out) == 1


def test_premium_filter_drops_low_conviction():
    rows = [_row(model_prob=0.65), _row(model_prob=0.50)]
    out = _premium_filter(rows, Tier.LEAN, 0.55, 0.0)
    assert len(out) == 1


def test_premium_filter_drops_low_edge():
    rows = [_row(edge_pp=8.0), _row(edge_pp=2.0)]
    out = _premium_filter(rows, Tier.LEAN, 0.0, 5.0)
    assert len(out) == 1


def test_premium_filter_sorts_by_edge_desc():
    rows = [_row(edge_pp=4.0), _row(edge_pp=12.0), _row(edge_pp=7.0)]
    out = _premium_filter(rows, Tier.LEAN, 0.0, 0.0)
    assert [r["edge_pp"] for r in out] == [12.0, 7.0, 4.0]


def test_premium_filter_handles_unknown_tier_label():
    # Defensive: unknown tier strings rank as 0 (filtered out).
    rows = [_row(tier="MYSTERY"), _row(tier="STRONG")]
    out = _premium_filter(rows, Tier.STRONG, 0.0, 0.0)
    assert len(out) == 1
    assert out[0]["tier"] == "STRONG"


def test_write_outputs_creates_three_files(tmp_path: Path):
    rows = [_row()]
    paths = write_outputs(
        output_dir=tmp_path,
        target_date="2026-05-09",
        all_rows=rows,
        todays_card=rows,
        thresholds={"min_tier": "STRONG", "min_conviction": 0.55,
                    "min_edge_pp": 5.0},
        n_lines_fetched=10, n_projected=20, n_skipped_low_conf=2,
    )
    assert paths["json"].exists()
    assert paths["todays_card_json"].exists()
    assert paths["csv"].exists()

    payload = json.loads(paths["json"].read_text())
    assert payload["counts"]["all_picks"] == 1
    assert payload["counts"]["todays_card"] == 1

    card = json.loads(paths["todays_card_json"].read_text())
    assert card["picks"][0]["player_name"] == "Aaron Judge"

    header = paths["csv"].read_text().splitlines()[0]
    for col in CSV_COLUMNS:
        assert col in header


def test_load_props_picks_into_premium(tmp_path: Path):
    payload = {
        "todays_card": [
            _row(player_name="Mookie Betts", model_prob=0.66, model_pct=66.0,
                 edge_pp=7.5, kelly_units=2.0, tier="STRONG"),
        ],
    }
    p = tmp_path / "props_daily.json"
    p.write_text(json.dumps(payload))
    picks = load_props_picks(p)
    assert len(picks) == 1
    pp = picks[0]
    assert pp.sport == "MLB-Props"
    assert pp.matchup == "Mookie Betts"
    assert pp.conviction_pct == pytest.approx(66.0)
    assert pp.edge_pct == pytest.approx(7.5)
    assert pp.kelly_pct == pytest.approx(2.0)


def test_load_props_picks_handles_missing_file(tmp_path: Path):
    assert load_props_picks(tmp_path / "missing.json") == []


def test_load_props_picks_empty_kelly_falls_through_to_pass(tmp_path: Path):
    payload = {
        "todays_card": [
            _row(kelly_units=None, model_pct=66.0, edge_pp=7.0),
        ],
    }
    p = tmp_path / "props_daily.json"
    p.write_text(json.dumps(payload))
    picks = load_props_picks(p)
    assert picks[0].kelly_advice == "PASS"


def test_filter_premium_drops_pass_kelly_props_picks(tmp_path: Path):
    payload = {
        "todays_card": [
            _row(kelly_units=None, model_pct=66.0, edge_pp=7.0),
        ],
    }
    p = tmp_path / "props_daily.json"
    p.write_text(json.dumps(payload))
    picks = load_props_picks(p)
    out = filter_premium(picks)
    assert out == []  # PASS kelly_advice should be dropped
