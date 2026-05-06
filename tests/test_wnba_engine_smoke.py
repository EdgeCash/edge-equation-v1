"""Smoke tests for the rebuilt WNBA engine.

These cover the imports + flow that were broken before the May-2026
rewrite (markdown-wrapped deterministic.py, missing IsotonicCalibrator,
broken `utils.logger` / `publishing.formatter` / `ingestion.wnba`
imports). Every assertion here is something a fresh checkout would have
hit ImportError or AttributeError on previously.
"""
from __future__ import annotations

from edge_equation.engines.wnba.model_bundle.deterministic import (
    DeterministicWNBA,
)
from edge_equation.engines.wnba.model_bundle.ml_bundle import (
    IsotonicCalibrator, WNBAMLBundle,
)
from edge_equation.engines.wnba.run_daily import WNBARunner
from edge_equation.engines.wnba.schema import EngineConfig, Market, Output


def test_schema_has_is_qualified():
    cfg = EngineConfig()
    out = Output(
        market=Market.POINTS, projection=22.0, line=20.0,
        edge=2.0, probability=0.62, confidence=0.24,
    )
    # 2 / 20 = 10% edge, 0.62 conviction -> qualified at default thresholds.
    assert out.is_qualified() is True
    # Tight thresholds rule it out.
    assert out.is_qualified(min_edge_pct=20.0) is False


def test_deterministic_module_compiles_to_a_class():
    cfg = EngineConfig()
    det = DeterministicWNBA(cfg)
    assert det.project_points(0.25, 70, 1.05) > 0
    scores = det.project_fullgame_scores(1.05, 1.00, 70)
    assert scores["team_score"] > scores["opp_score"]


def test_isotonic_calibrator_compat_wrapper():
    # The from_dict shape mirrors what the rest of the pipeline ships
    # in calibration JSON. An empty calibrator transforms identity.
    cal = IsotonicCalibrator.from_dict({"blocks": []})
    assert cal.transform(0.5) == 0.5


def test_wnba_runner_constructs_without_external_io():
    runner = WNBARunner()
    # Even with no slate on disk the runner should return cleanly.
    out = runner.run("1900-01-01")  # date that's never on disk
    assert isinstance(out, list)


def test_ml_bundle_falls_back_to_deterministic_without_models(tmp_path):
    cfg = EngineConfig()
    det = DeterministicWNBA(cfg)
    bundle = WNBAMLBundle(det, model_dir=str(tmp_path))  # empty dir
    out = bundle.predict(
        market=Market.POINTS,
        features={
            "usage": 0.25, "possessions": 70.0, "shot_quality": 1.05,
            "rebound_chance": 0.10, "minutes": 30.0, "assist_rate": 0.05,
            "three_rate": 0.10, "three_accuracy": 0.35,
            "team_ppp": 1.05, "opp_ppp": 1.0,
        },
        line=18.0,
        meta={"player": "Test", "team": "AAA", "opponent": "BBB"},
    )
    assert isinstance(out, Output)
    assert out.market == Market.POINTS
    assert out.model_version == "deterministic_v1"
