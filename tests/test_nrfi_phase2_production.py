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


def test_ledger_render_labels_yrfi_lean_independently():
    from edge_equation.engines.nrfi.ledger import render_ledger_section
    import pandas as pd

    class _Store:
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

    why = _why_note(["+4.1 pitcher_csw", "-2.0 park_factor"], 0.72, 3.5)
    assert "pitcher_csw" in why
    assert "λ=0.72" in why
    assert "MC ±3.5pp" in why


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
