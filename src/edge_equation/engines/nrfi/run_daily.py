"""Daily inference entry point.

Usage:

    python -m nrfi.run_daily              # today's slate
    python -m nrfi.run_daily 2026-04-27   # specific date
    python -m nrfi.run_daily --no-mc      # disable Monte Carlo refinement
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

from .config import get_default_config
from .data.scrapers_etl import daily_etl
from .data.storage import NRFIStore
from .evaluation.backtest import reconstruct_features_for_date
from .models.inference import NRFIInferenceEngine
from .models.model_training import MODEL_VERSION, TrainedBundle
from edge_equation.utils.logging import get_logger

log = get_logger(__name__, "INFO")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NRFI/YRFI daily inference")
    parser.add_argument("date", nargs="?",
                        default=date.today().isoformat(),
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-mc", action="store_true",
                        help="Disable Monte Carlo refinement")
    parser.add_argument("--no-shap", action="store_true",
                        help="Disable SHAP explanations")
    parser.add_argument("--output-json", default="nrfi_output.json",
                        help="Where to drop the per-game JSON")
    args = parser.parse_args(argv)

    cfg = get_default_config()
    if args.no_mc:
        cfg = replace(cfg, enable_monte_carlo=False)
    if args.no_shap:
        cfg = replace(cfg, enable_shap=False)
    cfg = cfg.resolve_paths()

    store = NRFIStore(cfg.duckdb_path)

    # Pull schedule + lineups + ump.
    n = daily_etl(args.date, store, config=cfg)
    log.info("Hydrated %d games for %s", n, args.date)

    # Build features for every game on the slate.
    feats_per_game = reconstruct_features_for_date(args.date, store=store, config=cfg)
    if not feats_per_game:
        log.warning("No games found for %s", args.date)
        return 0

    # Try to load a trained bundle; fall back to deterministic Poisson.
    bundle: TrainedBundle | None = None
    try:
        bundle = TrainedBundle.load(cfg.model_dir, MODEL_VERSION)
        log.info("Loaded model bundle %s", MODEL_VERSION)
    except Exception:
        log.warning("No trained bundle found at %s — using Poisson baseline",
                    cfg.model_dir)

    if bundle is not None:
        engine = NRFIInferenceEngine(bundle, cfg)
        feature_dicts = [f for _, f in feats_per_game]
        game_pks = [pk for pk, _ in feats_per_game]
        preds = engine.predict_many(feature_dicts, game_pks=game_pks)
        engine.attach_monte_carlo(preds, feature_dicts)
        rows = [p.as_row() for p in preds]
    else:
        # Deterministic fallback — Poisson baseline only.
        # Column set must match the ``predictions`` table schema in
        # ``data/storage.py``. The schema doesn't include ``color_hex``
        # (the website renders the band → color map itself), so we
        # omit it here. Same with any other UI-only column the
        # operator might be tempted to add — the predictions table
        # is the canonical model output, not a render cache.
        from .utils.colors import nrfi_band
        rows = []
        for pk, f in feats_per_game:
            p = float(f.get("poisson_p_nrfi", 0.55))
            band = nrfi_band(p * 100.0)
            rows.append({
                "game_pk": pk,
                "nrfi_prob": p,
                "nrfi_pct": round(p * 100.0, 1),
                "lambda_total": float(f.get("lambda_total", 1.0)),
                "color_band": band.label,
                "signal": band.signal,
                "model_version": "poisson_baseline_only",
            })

    if rows:
        store.upsert("predictions", rows)
        Path(args.output_json).write_text(json.dumps(rows, indent=2, default=str))
        log.info("Wrote %d predictions → %s", len(rows), args.output_json)

    # Pretty-print top-of-board.
    rows.sort(key=lambda r: -r["nrfi_prob"])
    print(f"\n=== NRFI Board {args.date} ===")
    for r in rows:
        print(f"  game_pk={r['game_pk']:>10}  NRFI {r['nrfi_pct']:>5.1f}%  "
              f"({r['color_band']:>11}) λ={r['lambda_total']:.2f}  signal={r['signal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
