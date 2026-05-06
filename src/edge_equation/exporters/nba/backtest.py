"""NBA game-results backtest CLI.

Thin wrapper around `_game_results_backtest.GameResultsBacktestEngine`
with `NBA_CONFIG`. See the shared module for the math + grading
contract.

Usage::

    python -m edge_equation.exporters.nba.backtest --seasons 2024 2025
    python -m edge_equation.exporters.nba.backtest --seasons 2024 2025 \\
        --output data/nba_backtest_summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from edge_equation.exporters._game_results_backtest import (
    GameResultsBacktestEngine, NBA_CONFIG, load_games_jsonl,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BACKFILL_DIR = REPO_ROOT / "data" / "backfill" / "nba"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="edge_equation.exporters.nba.backtest")
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--backfill-dir", type=Path, default=DEFAULT_BACKFILL_DIR)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = load_games_jsonl(args.seasons, args.backfill_dir)
    print(f"Loaded {len(rows):,} NBA games from seasons {args.seasons}")
    if not rows:
        print("No data on disk. Run scripts/backfill_nba_games.py first.")
        return 1
    eng = GameResultsBacktestEngine(rows=rows, cfg=NBA_CONFIG)
    res = eng.run()

    print()
    print("=== ALL bets ===")
    print(f"{'market':<12}  {'bets':>6}  {'hit%':>5}  {'ROI%':>7}  {'Brier':>7}")
    for r in sorted(res["summary_by_bet_type"], key=lambda r: -r["roi_pct"]):
        print(f"{r['bet_type']:<12}  {r['bets']:>6}  {r['hit_rate']:>5.1f}  "
              f"{r['roi_pct']:>+7.2f}  {r['brier']!s:>7}")
    print()
    print("=== PLAY-only ===")
    print(f"{'market':<12}  {'bets':>6}  {'hit%':>5}  {'ROI%':>7}  {'Brier':>7}  Gate")
    for r in sorted(res["summary_by_bet_type_play_only"], key=lambda r: -r["roi_pct"]):
        bri = r["brier"]
        passed = (
            r["bets"] >= 200 and r["roi_pct"] >= 1.0
            and bri is not None and bri < 0.246
        )
        flag = "PASS" if passed else "fail"
        print(f"{r['bet_type']:<12}  {r['bets']:>6}  {r['hit_rate']:>5.1f}  "
              f"{r['roi_pct']:>+7.2f}  {bri!s:>7}  {flag}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(res, indent=2, default=str))
        print(f"\nWrote summary to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
