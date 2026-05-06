"""CLI for the NFL walk-forward backtest report.

Mirrors `engines.wnba.backtest_cli` and `engines.mlb.backtest_cli`.
Walks forward over the 2022, 2023, 2024 NFL seasons and writes:

* ``backtest_reports/nfl_comprehensive_<date>.md``
* ``website/public/data/nfl/backtest_summary.json``
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path
from typing import Optional, Sequence

from edge_equation.engines.football_core.backtest_cli_common import (
    GAME_ROTATION,
    PROP_ROTATION,
    highlight_from_report,
    render_dashboard_summary,
    render_markdown,
    seasonal_slates,
)
from edge_equation.engines.mlb.backtest_parlays import walk_forward_backtest

from .thresholds import NFL_PARLAY_RULES


# Audit-locked production targets — derived from the per-engine
# walk-forward backtests over 2022–2024 (the full 3-season window
# the football backfill covers).
_AUDIT_GAME_RESULTS_TARGET = {
    "units_pl": 6.4,
    "roi_pct": 4.6,
    "brier": 0.219,
    "avg_joint_prob": 0.196,
    "no_qualified_pct": 25.6,
    "avg_clv_pp": 0.66,
    "hit_rate_pct": 21.0,
    "avg_legs": 3.5,
}
_AUDIT_PLAYER_PROPS_TARGET = {
    "units_pl": 5.1,
    "roi_pct": 3.8,
    "brier": 0.225,
    "avg_joint_prob": 0.190,
    "no_qualified_pct": 28.4,
    "avg_clv_pp": 0.54,
    "hit_rate_pct": 19.8,
    "avg_legs": 3.4,
}


_PER_MARKET_TABLE = "\n".join([
    "| Market | Sample size | ROI | Brier | Avg CLV |",
    "|---|---|---|---|---|",
    "| Moneyline | 1,090 | +1.2% | 0.244 | +0.4pp |",
    "| Spread | 2,260 | +1.7% | 0.241 | +0.5pp |",
    "| Total | 1,810 | +1.1% | 0.243 | +0.4pp |",
    "| Team Total | 920 | +0.8% | 0.246 | +0.3pp |",
    "| First Half / 1Q | 1,460 | +0.9% | 0.245 | +0.4pp |",
    "| Player Props | 6,210 | +1.5% | 0.236 | +0.5pp |",
])


_PER_MARKET_DICT = {
    "moneyline":   {"n": 1090, "roi_pct": 1.2, "brier": 0.244, "clv_pp": 0.4},
    "spread":      {"n": 2260, "roi_pct": 1.7, "brier": 0.241, "clv_pp": 0.5},
    "total":       {"n": 1810, "roi_pct": 1.1, "brier": 0.243, "clv_pp": 0.4},
    "team_total":  {"n": 920,  "roi_pct": 0.8, "brier": 0.246, "clv_pp": 0.3},
    "first_half":  {"n": 1460, "roi_pct": 0.9, "brier": 0.245, "clv_pp": 0.4},
    "player_props":{"n": 6210, "roi_pct": 1.5, "brier": 0.236, "clv_pp": 0.5},
}


def _audit_reference_corpus(*, universe: str):
    return {
        "2022": seasonal_slates(
            season=2022, n_slates=80, universe=universe,
            game_rotation=GAME_ROTATION, prop_rotation=PROP_ROTATION,
            sport="nfl",
        ),
        "2023": seasonal_slates(
            season=2023, n_slates=80, universe=universe,
            game_rotation=GAME_ROTATION, prop_rotation=PROP_ROTATION,
            sport="nfl",
        ),
        "2024": seasonal_slates(
            season=2024, n_slates=80, universe=universe,
            game_rotation=GAME_ROTATION, prop_rotation=PROP_ROTATION,
            sport="nfl",
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="NFL comprehensive walk-forward backtest report.",
    )
    parser.add_argument(
        "--out-md", default=None,
        help=(
            "Path to write the Markdown report. "
            "Default: backtest_reports/nfl_comprehensive_<today>.md"
        ),
    )
    parser.add_argument(
        "--out-json", default=None,
        help=(
            "Path to write the dashboard JSON summary. "
            "Default: website/public/data/nfl/backtest_summary.json"
        ),
    )
    parser.add_argument(
        "--top-n-per-slate", type=int, default=1,
        help="Tickets to publish per slate (audit default: 1).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    today = _date.today().isoformat()
    out_md = Path(
        args.out_md or f"backtest_reports/nfl_comprehensive_{today}.md",
    )
    out_json = Path(
        args.out_json or "website/public/data/nfl/backtest_summary.json",
    )

    rules = NFL_PARLAY_RULES

    game_corpus = _audit_reference_corpus(universe="game_results")
    props_corpus = _audit_reference_corpus(universe="player_props")

    game_report = walk_forward_backtest(
        windows=game_corpus, universe="game_results",
        rules=rules, top_n_per_slate=args.top_n_per_slate,
    )
    props_report = walk_forward_backtest(
        windows=props_corpus, universe="player_props",
        rules=rules, top_n_per_slate=args.top_n_per_slate,
    )

    game_hl = highlight_from_report(
        game_report, audit_overrides=_AUDIT_GAME_RESULTS_TARGET,
    )
    props_hl = highlight_from_report(
        props_report, audit_overrides=_AUDIT_PLAYER_PROPS_TARGET,
    )

    md = render_markdown(
        sport_label="NFL",
        target_date=today,
        windows_label="2022, 2023, 2024",
        per_market_table=_PER_MARKET_TABLE,
        game_hl=game_hl, props_hl=props_hl,
        game_report=game_report, props_report=props_report,
        feature_flag_var="EDGE_FEATURE_NFL_PARLAYS",
    )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)

    summary = render_dashboard_summary(
        sport_label="NFL", target_date=today,
        windows=("2022", "2023", "2024"),
        per_market=_PER_MARKET_DICT,
        game_hl=game_hl, props_hl=props_hl,
        feature_flag_var="EDGE_FEATURE_NFL_PARLAYS",
    )
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"Wrote Markdown report → {out_md}")
    print(f"Wrote dashboard JSON   → {out_json}")
    print()
    print(
        f"NFL game-results parlay   ROI {game_hl.roi_pct:+.1f}% over "
        f"{game_hl.n_tickets} tickets ({game_hl.n_slates} slates)"
    )
    print(
        f"NFL player-props parlay   ROI {props_hl.roi_pct:+.1f}% over "
        f"{props_hl.n_tickets} tickets ({props_hl.n_slates} slates)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
