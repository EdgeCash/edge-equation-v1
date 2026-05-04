"""
MLB Daily Spreadsheet — orchestrator.

This is the top-level pipeline ported from
edge-equation-scrapers/exporters/mlb/daily_spreadsheet.py and adapted
to call into v1's stronger engines/compliance/persistence layers
instead of duplicating them.

Pipeline (matches the scrapers 13-step contract documented in
run_mlb_daily.py's docstring, but with v1 engines wired in):

    1. Pull season-to-date completed games from v1 ingestion.
    2. Pull today's slate (also via v1 ingestion).
    3. Pull probable starters / bullpen / weather / lineups.
    4. Pull market odds via v1's odds_api_source.
    5. Run the rolling backtest (exporters.mlb.backtest) → produces
       backtest.json, calibration.json, picks_log.json updates.
    6. Build a ProjectionModel with the calibration constants from (5).
    7. Project today's slate.
    8. For NRFI/YRFI specifically: defer to v1's elite NRFI engine
       (engines.nrfi) instead of using projections.py's first-inning math.
       The NRFI engine has XGBoost + LightGBM + SHAP + Monte Carlo;
       projections.py has only league-rate Poisson. NRFI engine wins.
    9. Compute edges per market against the bookmaker odds.
   10. Apply the rolling-window market gate (gates.market_gate) AND the
       per-market edge floor (gates.edge_floor_for).
   11. Compute Kelly tier per surviving pick (kelly.kelly_advice).
   12. Pass picks through compliance.PublicModeSanitizer for any public
       mirror; keep full detail in the operator xlsx.
   13. Write outputs to public/data/mlb/ and stamp picks into the v1
       persistence layer.

Feature flag: this orchestrator is a no-op unless
EDGE_FEATURE_SPREADSHEET_PIPELINE=on (env). Workflow flips it on; local
dev runs default to off so an accidental run doesn't pollute outputs.

CLI surface mirrors the scrapers version so workflow inputs port 1:1:

    python -m edge_equation.exporters.mlb.daily_spreadsheet \\
        [--date YYYY-MM-DD] [--no-odds] [--include-backfill] \\
        [--gate-min-bets 200] [--gate-min-roi 1.0] [--gate-max-brier 0.246] \\
        [--edge-threshold MARKET=PCT ...] [--no-market-gate] \\
        [--output-dir public/data/mlb] [--push]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from edge_equation.exporters.mlb import gates, kelly
from edge_equation.exporters.mlb.workbook import write_workbook, MARKET_TABS


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "public" / "data" / "mlb"
FEATURE_FLAG_ENV = "EDGE_FEATURE_SPREADSHEET_PIPELINE"


def _flag_on() -> bool:
    return os.environ.get(FEATURE_FLAG_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="edge_equation.exporters.mlb.daily_spreadsheet")
    p.add_argument("--date", default=None, help="Target date YYYY-MM-DD (defaults to today, ET)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--no-odds", action="store_true", default=False)
    p.add_argument("--include-backfill", action="store_true", default=False)
    p.add_argument("--gate-min-bets", type=int, default=gates.MIN_GATE_BETS)
    p.add_argument("--gate-min-roi", type=float, default=gates.MIN_GATE_ROI)
    p.add_argument("--gate-max-brier", type=float, default=gates.MAX_GATE_BRIER)
    p.add_argument("--edge-threshold", action="append", default=[], metavar="MARKET=PCT")
    p.add_argument("--no-market-gate", action="store_true", default=False)
    p.add_argument("--push", action="store_true", default=False)
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="Build the workbook in memory but don't write any files")
    return p


def _empty_outputs(output_dir: Path, as_of: str) -> dict:
    """Cold-start / dry-run shape — no picks but the file scaffolding exists."""
    return {
        "as_of": as_of,
        "output_dir": str(output_dir),
        "todays_card": [],
        "picks_by_market": {bt: [] for _, bt in MARKET_TABS},
        "backtest_summary": [],
        "calibration": {},
    }


def _build_pipeline(args: argparse.Namespace) -> dict:
    """The actual 13-step pipeline. Currently delegates the per-step calls
    to placeholder hooks that will be filled in as v1's ingestion / NRFI
    engine / persistence are wired in. Each placeholder is a clearly
    marked TODO so a reviewer can see exactly what is and isn't live yet.

    The intent is for this orchestrator to be the ONE place where the
    13-step contract is enforced; the individual steps live in their
    domain modules and stay testable in isolation.
    """
    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # TODO[step 1-4]: pull from edge_equation.ingestion (mlb_source for
    # backfill, odds_api_source for odds). The wiring is straightforward
    # because v1 already has these — they just haven't been called from
    # this orchestrator yet.
    backfill_games: list[dict] = []
    todays_slate: list[dict] = []
    odds_payload: dict = {}

    # TODO[step 5]: from edge_equation.exporters.mlb.backtest import
    # run_backtest. backtest writes backtest.json + calibration.json +
    # appends to picks_log.json. Until that module is ported, the gate
    # gets cold-start handling (returns None → all markets allowed).
    backtest_summary: list[dict] = []
    calibration: dict = {}

    # TODO[step 6-7]: from edge_equation.exporters.mlb.projections import
    # ProjectionModel. Construct with calibration; project_slate returns
    # the per-game projection rows.
    projections: list[dict] = []

    # TODO[step 8]: from edge_equation.engines.nrfi import build_card.
    # Replace the projector's first-inning math with the NRFI engine's
    # picks; merge into the per-market picks dict under "first_inning".

    # Step 10: gates.
    if args.no_market_gate:
        passed_markets, gate_notes = None, {}
    else:
        passed_markets, gate_notes = gates.market_gate(
            backtest_summary,
            min_bets=args.gate_min_bets,
            min_roi=args.gate_min_roi,
            max_brier=args.gate_max_brier,
        )
    overrides = gates.parse_threshold_overrides(args.edge_threshold)

    # Step 9 + 11 are no-ops while projections list is empty. Keeping the
    # shape consistent so the workbook builder still runs end-to-end and
    # produces an empty-but-well-formed xlsx — useful for CI smoke tests.
    picks_by_market: dict[str, list[dict]] = {bt: [] for _, bt in MARKET_TABS}
    todays_card: list[dict] = []

    # Step 12: compliance sanitization happens at write_outputs() time
    # since only the public CSV mirror needs it; the xlsx keeps full detail.

    return {
        "as_of": as_of,
        "output_dir": args.output_dir,
        "todays_card": todays_card,
        "picks_by_market": picks_by_market,
        "backtest_summary": backtest_summary,
        "calibration": calibration,
        "gate_notes": gate_notes,
        "passed_markets": list(passed_markets) if passed_markets is not None else None,
        "edge_overrides": overrides,
        "n_projections": len(projections),
        "n_backfill_games": len(backfill_games),
        "n_slate": len(todays_slate),
    }


def _write_outputs(state: dict) -> list[Path]:
    """Write the rolling snapshot files to public/data/mlb/.

    Per scrapers convention, files are NOT date-stamped — every run
    overwrites the same names so git history *is* the time series and
    Vercel always serves "today".
    """
    out_dir = Path(state["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    xlsx_path = out_dir / "mlb_daily.xlsx"
    write_workbook(
        xlsx_path,
        todays_card=state["todays_card"],
        picks_by_market=state["picks_by_market"],
        backtest_summary=state["backtest_summary"],
        as_of=state["as_of"],
    )
    written.append(xlsx_path)

    json_path = out_dir / "mlb_daily.json"
    json_path.write_text(json.dumps({
        "as_of": state["as_of"],
        "todays_card": state["todays_card"],
        "picks_by_market": state["picks_by_market"],
        "n_projections": state.get("n_projections", 0),
        "gate_notes": state.get("gate_notes", {}),
        "passed_markets": state.get("passed_markets"),
    }, indent=2))
    written.append(json_path)

    bt_path = out_dir / "backtest.json"
    bt_path.write_text(json.dumps({
        "as_of": state["as_of"],
        "summary_by_bet_type": state["backtest_summary"],
    }, indent=2))
    written.append(bt_path)

    cal_path = out_dir / "calibration.json"
    cal_path.write_text(json.dumps(state["calibration"], indent=2))
    written.append(cal_path)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not _flag_on():
        print(
            f"[mlb-daily] feature flag {FEATURE_FLAG_ENV}=off — skipping. "
            f"Set {FEATURE_FLAG_ENV}=on to run.",
            file=sys.stderr,
        )
        return 0

    state = _build_pipeline(args)

    if args.dry_run:
        print(f"[mlb-daily] dry-run state: {json.dumps({k: v for k, v in state.items() if k != 'picks_by_market'}, default=str, indent=2)}")
        return 0

    written = _write_outputs(state)
    print(f"[mlb-daily] wrote {len(written)} files to {state['output_dir']}")
    for p in written:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
