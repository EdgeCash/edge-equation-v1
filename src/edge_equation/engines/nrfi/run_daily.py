"""Daily inference entry point.

Usage:

    python -m edge_equation.engines.nrfi.run_daily
    python -m edge_equation.engines.nrfi.run_daily 2026-04-27
    python -m edge_equation.engines.nrfi.run_daily --no-mc

Legacy ``python -m nrfi.run_daily`` invocations are supported by deployment
wrappers that point at this package path.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from .config import get_default_config
from .data.scrapers_etl import daily_etl
from .data.storage import NRFIStore
from .evaluation.backtest import reconstruct_features_for_date
from .ledger import render_ledger_section
from .models.inference import NRFIInferenceEngine
from .models.model_training import MODEL_VERSION, TrainedBundle
from .ledger import render_ledger_section
from .output import build_output
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
        rows = []
        for pk, f in feats_per_game:
            p = float(f.get("poisson_p_nrfi", 0.55))
            out = build_output(
                game_id=str(pk),
                blended_p=p,
                lambda_total=float(f.get("lambda_total", 1.0)),
                engine="poisson_baseline",
                model_version="poisson_baseline_only",
            )
            rows.append({
                "game_pk": pk,
                "nrfi_prob": out.nrfi_prob,
                "nrfi_pct": out.nrfi_pct,
                "lambda_total": out.lambda_total,
                "color_band": out.color_band,
                "color_hex": out.color_hex,
                "signal": out.signal,
                "mc_low": out.mc_low,
                "mc_high": out.mc_high,
                "mc_band_pp": out.mc_band_pp,
                "shap_drivers": json.dumps(out.shap_drivers),
                "driver_text": json.dumps(out.driver_text),
                "market_prob": out.market_prob,
                "edge": out.edge,
                "edge_pp": out.edge_pp,
                "kelly_units": out.kelly_units,
                "kelly_suggestion": out.kelly_suggestion,
                "tier": out.tier,
                "tier_basis": out.tier_basis,
                "tier_value": out.tier_value,
                "tier_band": out.tier_band,
                "probability_display": out.headline(),
                "model_version": out.model_version,
            })

    if rows:
        store.upsert("predictions", rows)
        Path(args.output_json).write_text(json.dumps(rows, indent=2, default=str))
        log.info("Wrote %d predictions → %s", len(rows), args.output_json)

    print(render_ledger_section(store, season=int(args.date[:4])) or
          f"YTD LEDGER ({args.date[:4]}): no settled picks yet")
    _print_top_board(rows, args.date, baseline_fallback=bundle is None)
    return 0


def _row_sort_strength(row: dict) -> float:
    """Rank by edge when available, otherwise distance from coin-flip."""
    edge = row.get("edge")
    if edge is not None:
        return float(edge)
    return abs(float(row.get("nrfi_prob", 0.5)) - 0.5)


def _decode_driver_text(row: dict) -> str:
    raw = row.get("driver_text") or ""
    if isinstance(raw, list):
        return ", ".join(str(x) for x in raw[:3])
    if isinstance(raw, str) and raw:
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return ", ".join(str(x) for x in val[:3])
        except Exception:
            return raw
    return ""


def _print_top_board(
    rows: list[dict], game_date: str, *, baseline_fallback: bool = False,
) -> None:
    """Print a compact production board: Top 6 by edge-strength."""
    ranked = sorted(rows, key=_row_sort_strength, reverse=True)[:6]
    print(f"\n=== NRFI Elite Board {game_date} - Top 6 by edge ===")
    if baseline_fallback:
        print("DISCLAIMER: trained calibrated bundle unavailable; using Poisson baseline.")
    for idx, r in enumerate(ranked, start=1):
        prob = r.get("probability_display") or f"{float(r['nrfi_pct']):.1f}% NRFI"
        mc = (
            f"±{float(r['mc_band_pp']):.1f}pp"
            if r.get("mc_band_pp") is not None else "MC --"
        )
        edge = (
            f"{float(r['edge_pp']):+.1f}pp"
            if r.get("edge_pp") is not None else "edge n/a"
        )
        drivers = _decode_driver_text(r) or "drivers pending"
        print(
            f"{idx:>2}. game_pk={int(r['game_pk']):>10}  {prob:<11} "
            f"{str(r.get('tier', 'NO_PLAY')):<8} {str(r.get('color_hex', '')):<7} "
            f"λ={float(r['lambda_total']):.2f}  {mc:<8} {edge:<10} "
            f"Kelly={r.get('kelly_suggestion') or 'Market unavailable'}"
        )
        print(f"    Why: {drivers}; λ={float(r['lambda_total']):.2f}")


if __name__ == "__main__":
    raise SystemExit(main())
