"""Focused tests for NRFI Phase 2 production surfaces."""

from __future__ import annotations

import json
import pytest


def test_reliability_from_jsonl_splits_2025_and_2026(tmp_path):
    from edge_equation.engines.nrfi.models.train import _reliability_from_jsonl

    path = tmp_path / "walkforward_calibration.jsonl"
    rows = [
        {"game_date": "2025-04-01", "predicted_p": 0.70, "actual_y": 1},
        {"game_date": "2025-04-02", "predicted_p": 0.60, "actual_y": 0},
        {"game_date": "2026-04-01", "predicted_p": 0.80, "actual_y": 1},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))

    slices = _reliability_from_jsonl(str(path))
    by_label = {s.label: s for s in slices}

    assert by_label["2025"].n == 2
    assert by_label["2026"].n == 1
    assert by_label["2025"].bins


def test_training_manifest_serialises_policy(tmp_path):
    from edge_equation.engines.nrfi.models.train import (
        ProductionTrainingReport,
        _write_manifest,
    )
    from edge_equation.engines.nrfi.training.walkforward import WalkForwardReport

    class _Cfg:
        model_dir = tmp_path

    report = ProductionTrainingReport(
        start_date="2025-01-01",
        end_date="2026-04-29",
        window_months=18,
        chunk_days=7,
        calibration_method="isotonic",
        walkforward=WalkForwardReport(n_predictions=10, walkforward_brier=0.21),
    )

    manifest = _write_manifest(_Cfg(), report)
    payload = json.loads(manifest.read_text())
    assert payload["window_months"] == 18
    assert payload["chunk_days"] == 7
    assert payload["calibration_method"] == "isotonic"
    assert payload["walkforward"]["n_predictions"] == 10
    assert payload["artifact_version"] == "elite_nrfi_v1_20260429_wf10"


def test_full_corpus_date_selection_uses_prior_rows():
    from edge_equation.engines.nrfi.models.train import _select_full_corpus_window

    rows = [
        ("2025-04-01", 50),
        ("2025-04-02", 75),
        ("2025-04-03", 80),
    ]

    assert _select_full_corpus_window(rows, min_train_rows=100) == (
        "2025-04-03",
        "2025-04-03",
        205,
    )


def test_ledger_render_labels_yrfi_lean_independently():
    from edge_equation.engines.nrfi.ledger import render_ledger_section
    import pandas as pd

    class _Store:
        def execute(self, sql, params=()):
            return None

        def query_df(self, sql, params=()):
            return pd.DataFrame([
                {
                    "season": 2026,
                    "market_type": "YRFI",
                    "tier": "LEAN",
                    "n_settled": 3,
                    "wins": 2,
                    "losses": 1,
                    "units_won": 0.90,
                    "last_updated": "2026-04-29",
                }
            ])

    text = render_ledger_section(_Store(), season=2026)
    assert "YRFI_LEAN" in text
    assert "2-1" in text


def test_daily_report_sort_and_why_note():
    from edge_equation.engines.nrfi.email_report import _top_six_by_edge, _why_note

    picks = [
        {"game_id": "low", "edge_pp": 1.0, "pct": 60.0},
        {"game_id": "high", "edge_pp": 8.0, "pct": 58.0},
        {"game_id": "fallback", "edge_pp": None, "pct": 71.0},
    ]
    ranked = _top_six_by_edge(picks)
    assert ranked[0]["game_id"] == "fallback"
    assert ranked[1]["game_id"] == "high"

    why = _why_note(
        ["+8 from strong first-inning pitcher xFIP", "-6 from opposing top-3 OBP"],
        0.72,
        3.5,
    )
    assert "first-inning pitcher xFIP" in why
    assert "lambda=0.72" in why
    assert "MC +/-3.5pp" in why


def test_shared_core_parlay_facade_builds_candidates():
    from edge_equation.engines.core.math.parlay import (
        ParlayLeg,
        build_parlay_candidates,
    )
    from edge_equation.engines.tiering import Tier

    legs = [
        ParlayLeg("NRFI", "Under 0.5", 0.85, -115, Tier.LOCK, game_id="g1"),
        ParlayLeg("NRFI", "Under 0.5", 0.84, -110, Tier.LOCK, game_id="g2"),
    ]
    candidates = build_parlay_candidates(legs)
    assert candidates
    assert candidates[0].n_legs == 2


def test_prediction_schema_accepts_ml_audit_columns(tmp_path):
    from edge_equation.engines.nrfi.data.storage import NRFIStore

    store = NRFIStore(tmp_path / "nrfi.duckdb")
    store.upsert("predictions", [{
        "game_pk": 1,
        "model_version": "elite_nrfi_v1",
        "nrfi_prob": 0.61,
        "nrfi_pct": 61.0,
        "lambda_total": 0.92,
        "color_band": "Light Green",
        "color_hex": "#7cb342",
        "signal": "LEAN_NRFI",
        "poisson_p_nrfi": 0.58,
        "ml_p_nrfi": 0.63,
        "blended_p_nrfi": 0.61,
        "sort_edge": 0.11,
    }])
    df = store.query_df("SELECT poisson_p_nrfi, ml_p_nrfi, blended_p_nrfi, sort_edge FROM predictions")
    assert float(df.iloc[0].blended_p_nrfi) == 0.61


def test_email_market_inputs_use_captured_nrfi_odds(monkeypatch):
    from edge_equation.engines.nrfi import email_report

    monkeypatch.setattr(
        email_report,
        "lookup_closing_odds",
        lambda store, game_pk, market_type: -120.0 if game_pk == 1 else None,
    )

    market_probs, american_odds = email_report._market_inputs_for_games(
        object(),
        [1, 2],
    )

    assert market_probs[0] == pytest.approx(120 / 220)
    assert market_probs[1] is None
    assert american_odds == [-120.0, -110.0]


def test_full_corpus_training_selects_start_after_min_rows(monkeypatch, tmp_path):
    from edge_equation.engines.nrfi.models import train as train_mod
    import pandas as pd

    class _Store:
        def __init__(self, path):
            pass

        def query_df(self, sql):
            return pd.DataFrame([
                {"game_date": "2025-04-01", "n": 100},
                {"game_date": "2025-04-02", "n": 75},
                {"game_date": "2025-04-03", "n": 50},
                {"game_date": "2025-04-04", "n": 25},
            ])

    class _Cfg:
        duckdb_path = tmp_path / "nrfi.duckdb"

        def resolve_paths(self):
            return self

    captured = {}

    def _fake_train(**kwargs):
        captured.update(kwargs)
        return "report"

    monkeypatch.setattr(train_mod, "NRFIStore", _Store)
    monkeypatch.setattr(train_mod, "train_production_model", _fake_train)

    out = train_mod.train_full_available_corpus(
        min_train_rows=175,
        config=_Cfg(),
        quiet=True,
    )

    assert out == "report"
    assert captured["start_date"] == "2025-04-03"
    assert captured["end_date"] == "2025-04-04"


def test_daily_sort_prefers_edge_then_highest_nrfi_probability():
    from edge_equation.engines.nrfi.run_daily import _row_sort_strength

    assert _row_sort_strength({"edge": 0.08, "nrfi_prob": 0.55}) == 0.08
    # Without odds/edge, Top 6 should be highest NRFI probability, not distance
    # from 50%. This keeps YRFI-leaning 43% games off the NRFI board.
    assert _row_sort_strength({"edge": None, "nrfi_prob": 0.59}) > \
        _row_sort_strength({"edge": None, "nrfi_prob": 0.43})


def test_live_odds_status_reports_missing_key(monkeypatch):
    from edge_equation.engines.nrfi.run_daily import _pull_live_odds

    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    status = _pull_live_odds(object(), "2026-04-29", config=object())

    assert status.odds_api_available is False
    assert "key unavailable" in status.message


def test_top_board_renders_tier_color_and_odds_status(capsys):
    from edge_equation.engines.nrfi.run_daily import LiveOddsStatus, _print_top_board

    _print_top_board(
        [{
            "game_pk": 1,
            "probability_display": "59.0% NRFI",
            "nrfi_prob": 0.59,
            "tier": "MODERATE",
            "color_band": "Light Green",
            "lambda_total": 0.42,
            "mc_band_pp": 2.4,
            "edge_pp": 5.1,
            "kelly_suggestion": "0.35u",
            "driver_text": "+8 from wind blowing in",
        }],
        "2026-04-29",
        odds_status=LiveOddsStatus(
            nrfi_snapshots=4,
            props_games=12,
            odds_api_available=True,
        ),
    )
    out = capsys.readouterr().out
    assert "Odds API: 4 NRFI/YRFI snapshots, 12 prop games" in out
    assert "MODERATE (Light Green)" in out
    assert "edge +5.1pp" in out
    assert "Kelly=0.35u" in out


def test_human_driver_notes_are_readable_and_capped():
    from edge_equation.engines.nrfi.output.drivers import format_driver_notes

    notes = format_driver_notes([
        ("home_p_xera", -0.80),
        ("vs_home_p_top3_obp", 0.31),
        ("wx_wind_in_mph", 0.24),
        ("int_temp_x_park_runs", -0.20),
    ])

    assert notes[0] == "-14 from home starter xERA"
    assert notes[1] == "+9 from opposing lineup vs home starter top-3 OBP"
    assert notes[2] == "+7 from wind blowing in"
    assert all("_" not in n for n in notes)


def test_undersized_manifest_shrinks_model_weight(tmp_path):
    from edge_equation.engines.nrfi.models.inference import _model_quality_profile

    manifest = tmp_path / "elite_nrfi_v1_training_manifest.json"
    manifest.write_text('{"walkforward": {"n_predictions": 61}}')
    profile = _model_quality_profile(0.65, tmp_path)

    assert profile.blend_weight == pytest.approx(0.10)
    assert profile.use_poisson_only_baseline is True


def test_daily_sort_prefers_highest_nrfi_without_market_edge():
    from edge_equation.engines.nrfi.run_daily import _row_sort_strength

    assert _row_sort_strength({"nrfi_prob": 0.59, "edge": None}) > \
        _row_sort_strength({"nrfi_prob": 0.41, "edge": None})


def test_live_odds_pull_uses_capture_and_props_count(monkeypatch):
    from edge_equation.engines.nrfi import run_daily

    monkeypatch.setenv("THE_ODDS_API_KEY", "TEST")
    monkeypatch.setattr(run_daily, "capture_closing_lines", lambda *a, **kw: 4)
    monkeypatch.setattr(run_daily, "_pull_player_prop_odds_count", lambda: 9)

    status = run_daily._pull_live_odds(object(), "2026-04-29", config=object())

    assert status.odds_api_available is True
    assert status.nrfi_snapshots == 4
    assert status.props_games == 9
