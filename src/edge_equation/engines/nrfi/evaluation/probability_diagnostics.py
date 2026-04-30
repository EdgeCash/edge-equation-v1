"""Probability-distribution diagnostics for NRFI training/live runs.

This is intentionally lightweight and read-only.  It helps answer "are we
compressed because the model has no signal, because calibration is too strong,
or because blending is flattening the output?"
"""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbabilitySummary:
    label: str
    n: int
    min_pct: float
    p10_pct: float
    mean_pct: float
    p90_pct: float
    max_pct: float
    std_pct: float
    ge_55: int
    ge_58: int
    ge_64: int
    ge_70: int

    def line(self) -> str:
        return (
            f"{self.label:<12} n={self.n:<4} "
            f"min={self.min_pct:5.1f}% p10={self.p10_pct:5.1f}% "
            f"mean={self.mean_pct:5.1f}% p90={self.p90_pct:5.1f}% "
            f"max={self.max_pct:5.1f}% std={self.std_pct:4.1f}% "
            f">=55/{self.ge_55} >=58/{self.ge_58} >=64/{self.ge_64} >=70/{self.ge_70}"
        )


def summarize_probabilities(label: str, probabilities: Sequence[float]) -> ProbabilitySummary:
    """Summarize a probability vector for calibration/debug output."""

    vals = sorted(float(p) for p in probabilities)
    if not vals:
        return ProbabilitySummary(label, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    n = len(vals)
    mean = sum(vals) / n
    var = sum((p - mean) ** 2 for p in vals) / n

    def q(frac: float) -> float:
        idx = min(n - 1, max(0, round((n - 1) * frac)))
        return vals[idx] * 100.0

    return ProbabilitySummary(
        label=label,
        n=n,
        min_pct=vals[0] * 100.0,
        p10_pct=q(0.10),
        mean_pct=mean * 100.0,
        p90_pct=q(0.90),
        max_pct=vals[-1] * 100.0,
        std_pct=(var ** 0.5) * 100.0,
        ge_55=sum(1 for p in vals if p >= 0.55),
        ge_58=sum(1 for p in vals if p >= 0.58),
        ge_64=sum(1 for p in vals if p >= 0.64),
        ge_70=sum(1 for p in vals if p >= 0.70),
    )


def render_probability_summaries(summaries: Iterable[ProbabilitySummary]) -> str:
    lines = ["Probability distribution diagnostics", "-" * 80]
    lines.extend(s.line() for s in summaries)
    return "\n".join(lines)


def diagnostics_for_date(target_date: str):
    """Build raw/calibrated/Poisson/blended summaries for one slate."""

    import numpy as np
    import pandas as pd

    from ..config import get_default_config
    from ..data.storage import NRFIStore
    from ..evaluation.backtest import reconstruct_features_for_date
    from ..models.inference import NRFIInferenceEngine
    from ..models.model_training import MODEL_VERSION, TrainedBundle

    cfg = get_default_config().resolve_paths()
    store = NRFIStore(cfg.duckdb_path)
    bundle = TrainedBundle.load(cfg.model_dir, MODEL_VERSION)
    feats = reconstruct_features_for_date(target_date, store=store, config=cfg)
    if not feats:
        return []

    feature_dicts = [f for _, f in feats]
    df = pd.DataFrame(feature_dicts).fillna(0.0)
    for col in bundle.feature_names:
        if col not in df.columns:
            df[col] = 0.0
    x = df[bundle.feature_names]
    raw = bundle.classifier._raw_predict(x)
    calibrated = bundle.classifier.predict_proba(x)
    lam = bundle.regressor.predict_lambda(x)
    lam_p = np.exp(-np.maximum(lam, 0.0))
    poisson = np.asarray([
        float(f.get("poisson_p_nrfi", np.exp(-float(f.get("lambda_total", 1.0)))))
        for f in feature_dicts
    ])
    engine = NRFIInferenceEngine(bundle, cfg)
    preds = engine.predict_many(feature_dicts, game_pks=[pk for pk, _ in feats])
    blended = [p.nrfi_prob for p in preds]
    return [
        summarize_probabilities("raw_xgb", raw),
        summarize_probabilities("calibrated", calibrated),
        summarize_probabilities("poisson", poisson),
        summarize_probabilities("lambda_head", lam_p),
        summarize_probabilities("blended", blended),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect NRFI probability spread.")
    parser.add_argument("--date", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(render_probability_summaries(diagnostics_for_date(args.date)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

