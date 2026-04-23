"""
That K Report -- debug / metrics artifact.

One JSON file per projections run capturing everything the main
Edge Equation engine needs to mine for future tuning:

  * model_version + run date + target account
  * full per-pitcher projection rows (mean, line, grade, edge, p10-p90,
    projected_mean for both NB+MC and Beta-Binomial variants)
  * slate-level feature-importance aggregate (mean share per factor,
    lead-count per factor)
  * A/B variant summary (MAE for each variant once actuals are available;
    None before settlement)

The file is opt-in via the CLI `--metrics-out <path>` flag so routine
manual runs stay lightweight; the scheduled testing-ground workflow
always writes it so calibration evidence accumulates over time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from edge_equation.that_k.config import TargetAccount, target_header_tag
from edge_equation.that_k.features import (
    FeatureImportanceRow,
    aggregate_importance,
)
from edge_equation.that_k.report import KReportRow
from edge_equation.that_k.variants import ABEntry, ab_summary


METRICS_MODEL_VERSION = "that_k-0.3"


def build_metrics_payload(
    rows: Sequence[KReportRow],
    ab_entries: Sequence[ABEntry],
    feature_rows: Sequence[FeatureImportanceRow],
    date_str: str,
    target_account: TargetAccount,
) -> dict:
    """Compose the full metrics dict.  Deterministic given the same
    inputs so two identical dry-runs produce byte-identical JSON."""
    per_pitcher: List[dict] = []
    # Zip by slate order -- build_projections, build_ab_entries, and
    # build_feature_importance all iterate the same slate order.
    ab_by_pitcher = {e.pitcher: e for e in ab_entries}
    fi_by_pitcher = {f.pitcher: f for f in feature_rows}
    for r in rows:
        p = r.projection
        ab = ab_by_pitcher.get(r.pitcher.name)
        fi = fi_by_pitcher.get(r.pitcher.name)
        per_pitcher.append({
            "pitcher": r.pitcher.name,
            "team": r.pitcher.team,
            "opponent": r.lineup.team,
            "line": str(p.line),
            "grade": r.grade,
            "projection": {
                "nb_mc_mean": str(p.mean),
                "stdev": str(p.stdev),
                "p10": str(p.p10),
                "p50": str(p.p50),
                "p90": str(p.p90),
                "prob_over": str(p.prob_over),
                "prob_under": str(p.prob_under),
                "edge_prob": str(p.edge_prob),
                "edge_ks": str(p.edge_ks),
                "n_sims": p.n_sims,
            },
            "ab_variant": ab.to_dict() if ab else None,
            "feature_importance": fi.to_dict() if fi else None,
            "sample_warning": r.inputs.sample_warning,
        })

    return {
        "model_version": METRICS_MODEL_VERSION,
        "run_date": date_str,
        "target_account": target_account.value,
        "target_tag": target_header_tag(target_account),
        "n_pitchers": len(rows),
        "feature_importance_aggregate": aggregate_importance(feature_rows),
        "ab_summary": ab_summary(list(ab_entries)),
        "pitchers": per_pitcher,
    }


def write_metrics(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )
