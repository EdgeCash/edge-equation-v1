"""Historical backtest CLI.

Usage:

    # Replay last 7 days against the deterministic baseline
    python -m nrfi.backtest_historical

    # Replay a specific window with a trained bundle
    python -m nrfi.backtest_historical 2024-04-01 2024-09-30 --use-model

    # Save plots / CSV
    python -m nrfi.backtest_historical 2024-04-01 2024-04-30 --save-dir reports/
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from .config import get_default_config
from .evaluation.backtest import backtest_range
from .models.model_training import MODEL_VERSION, TrainedBundle
from .utils.logging import get_logger

log = get_logger(__name__, "INFO")


def main(argv: list[str] | None = None) -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description="NRFI/YRFI historical backtest")
    parser.add_argument("start", nargs="?",
                        default=(today - timedelta(days=7)).isoformat())
    parser.add_argument("end", nargs="?",
                        default=(today - timedelta(days=1)).isoformat())
    parser.add_argument("--use-model", action="store_true",
                        help="Load trained bundle (default: Poisson baseline only)")
    parser.add_argument("--save-dir", default=None,
                        help="Directory to drop CSV + PNGs")
    args = parser.parse_args(argv)

    cfg = get_default_config()
    bundle = None
    if args.use_model:
        try:
            bundle = TrainedBundle.load(cfg.model_dir, MODEL_VERSION)
            log.info("Loaded bundle %s", MODEL_VERSION)
        except Exception as e:
            log.warning("Could not load bundle, using baseline: %s", e)

    report = backtest_range(
        args.start, args.end, config=cfg, bundle=bundle,
        save_dir=Path(args.save_dir) if args.save_dir else None,
    )

    print(f"\n=== Backtest {args.start} → {args.end} ===")
    print(f"Games scored : {report.n_games}")
    print(f"Base NRFI    : {report.base_rate:.3f}")
    print(f"Accuracy@.5  : {report.accuracy:.3f}")
    print(f"Brier        : {report.brier:.4f}")
    print(f"Log loss     : {report.log_loss:.4f}")
    if report.roi_flat:
        r = report.roi_flat
        print(f"\n--- ROI (flat 1u, edge≥4%, side=auto) ---")
        print(f"Bets {r.bets}  Wins {r.wins}  Units staked {r.units_staked:.1f}")
        print(f"Units won {r.units_won:+.2f}  ROI {r.roi_pct:+.2f}%  avg edge {r.avg_edge_pct:.2f}pp")

    print("\n--- Reliability buckets (predicted → actual, n) ---")
    edges = report.reliability.get("edges", [])
    pm = report.reliability.get("predicted", [])
    am = report.reliability.get("actual", [])
    cnt = report.reliability.get("count", [])
    for i in range(len(pm)):
        lo = edges[i] * 100; hi = edges[i + 1] * 100 if i + 1 < len(edges) else 100.0
        p = pm[i] * 100 if pm[i] is not None else float('nan')
        a = am[i] * 100 if am[i] == am[i] else float('nan')
        print(f"  [{lo:5.1f}%-{hi:5.1f}%]  pred {p:5.1f}%  actual {a:5.1f}%  n={cnt[i]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
