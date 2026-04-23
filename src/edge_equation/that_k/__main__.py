"""
That K Report -- CLI entry.

    # Dry-run against the built-in sample slate (8 MLB starters).
    python -m edge_equation.that_k --sample

    # With a custom run date (defaults to today, local clock).
    python -m edge_equation.that_k --sample --date 2026-04-23

    # Load a slate from JSON (same shape as sample_slate.sample_slate()).
    python -m edge_equation.that_k --slate path/to/slate.json

    # Save the rendered text to a file (workflow target).
    python -m edge_equation.that_k --sample --out data/thatk_report.txt

The CLI stays side-project-clean: no DB writes, no publisher glue.
A daily workflow just pipes the stdout text into the posting tool
(or email transport) of choice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import List

from edge_equation.that_k.report import DEFAULT_TOP_N, render_report
from edge_equation.that_k.runner import build_projections
from edge_equation.that_k.sample_slate import sample_slate
from edge_equation.that_k.simulator import DEFAULT_N_SIMS


def _load_slate(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(
            f"Slate JSON at {path} must be a list of row objects."
        )
    return data


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="edge_equation.that_k",
        description="Generate the That K Report for a slate of MLB starters.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--sample", action="store_true",
        help="Use the built-in deterministic 8-pitcher sample slate.",
    )
    src.add_argument(
        "--slate", type=Path,
        help="Path to a JSON slate file (list of rows).",
    )
    p.add_argument(
        "--date", dest="date",
        default=dt.date.today().isoformat(),
        help="Run date (YYYY-MM-DD) -- appears in the report header.",
    )
    p.add_argument(
        "--top-n", type=int, default=DEFAULT_TOP_N,
        help="Cap the report to the top-N highest-edge starters (default 8).",
    )
    p.add_argument(
        "--n-sims", type=int, default=DEFAULT_N_SIMS,
        help="Monte Carlo sample count per pitcher (default 5000).",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Optional output file path. When omitted the report prints "
             "to stdout.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.sample:
        slate = sample_slate()
    else:
        slate = _load_slate(args.slate)
    rows = build_projections(slate, n_sims=args.n_sims)
    report = render_report(rows, date_str=args.date, top_n=args.top_n)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
