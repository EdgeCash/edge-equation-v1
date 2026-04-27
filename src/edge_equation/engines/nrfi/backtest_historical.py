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
from edge_equation.utils.logging import get_logger

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
    parser.add_argument("--forecast-weather", action="store_true",
                        help="Use the Open-Meteo forecast endpoint snapped to T-3hr "
                              "(matches what the daily run sees pre-game)")
    parser.add_argument("--green-only-roi", action="store_true",
                        help="Restrict ROI sim to green/red high-confidence picks only")
    parser.add_argument("--green-threshold", type=float, default=0.70,
                        help="Probability threshold for 'green' picks (default 0.70)")
    parser.add_argument("--no-summary-table", action="store_true",
                        help="Skip the boxed summary table at the end of the report")
    parser.add_argument("--reliability-summary", action="store_true",
                        help="Print the bin-level reliability summary "
                              "('70-80%% bin: actual hit rate 74%%')")
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
        forecast_weather_only=args.forecast_weather,
        roi_green_only=args.green_only_roi,
        green_threshold=args.green_threshold,
    )

    print(f"\n=== Backtest {args.start} → {args.end} ===")
    print(f"Games scored : {report.n_games}")
    print(f"Base NRFI    : {report.base_rate:.3f}")
    print(f"Accuracy@.5  : {report.accuracy:.3f}")
    print(f"Brier        : {report.brier:.4f}")
    print(f"Log loss     : {report.log_loss:.4f}")

    if report.regimes:
        print("\n--- Regime split (pre-ABS vs ABS-era) ---")
        for rg in report.regimes:
            print(f"  {rg.label:>20}  n={rg.n_games:>5}  base={rg.base_rate:.3f}  "
                  f"acc={rg.accuracy:.3f}  brier={rg.brier:.4f}  ll={rg.log_loss:.4f}")

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

    if args.reliability_summary and not report.per_game.empty:
        from .calibration import reliability_summary
        df = report.per_game
        reliability_summary(
            df["p_nrfi"].astype(float).values,
            df["actual_nrfi"].astype(int).values,
            n_bins=10, brier=report.brier, print_to_stdout=True,
        )

    if not args.no_summary_table:
        from .evaluation.backtest import summary_table_str
        print()
        print(summary_table_str(report, green_threshold=args.green_threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
