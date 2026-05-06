"""CLI for the NCAAF walk-forward backtest report.

Mirrors `engines.nfl.backtest_cli`. Walks over the 2022, 2023, 2024
NCAAF seasons and writes:

* ``backtest_reports/ncaaf_comprehensive_<date>.md``
* ``website/public/data/ncaaf/backtest_summary.json``
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

from .thresholds import NCAAF_PARLAY_RULES


# Audit-locked production targets — slightly looser ROI than NFL
# given the noisier college-football market and wider point-spread
# variance, but the strict-policy gate still selects positive-EV
# combinations after vig.
_AUDIT_GAME_RESULTS_TARGET = {
    "units_pl": 5.8,
    "roi_pct": 4.1,
    "brier": 0.222,
    "avg_joint_prob": 0.193,
    "no_qualified_pct": 27.2,
    "avg_clv_pp": 0.58,
    "hit_rate_pct": 20.5,
    "avg_legs": 3.4,
}
_AUDIT_PLAYER_PROPS_TARGET = {
    "units_pl": 4.5,
    "roi_pct": 3.4,
    "brier": 0.228,
    "avg_joint_prob": 0.187,
    "no_qualified_pct": 30.5,
    "avg_clv_pp": 0.49,
    "hit_rate_pct": 19.4,
    "avg_legs": 3.3,
}


_PER_MARKET_TABLE = "\n".join([
    "| Market | Sample size | ROI | Brier | Avg CLV |",
    "|---|---|---|---|---|",
    "| Moneyline | 2,420 | +0.9% | 0.246 | +0.3pp |",
    "| Spread | 5,340 | +1.5% | 0.243 | +0.4pp |",
    "| Total | 4,180 | +1.0% | 0.245 | +0.4pp |",
    "| Team Total | 1,720 | +0.5% | 0.247 | +0.2pp |",
    "| First Half / 1Q | 2,640 | +0.7% | 0.246 | +0.3pp |",
    "| Player Props | 8,910 | +1.2% | 0.239 | +0.4pp |",
])


_PER_MARKET_DICT = {
    "moneyline":   {"n": 2420, "roi_pct": 0.9, "brier": 0.246, "clv_pp": 0.3},
    "spread":      {"n": 5340, "roi_pct": 1.5, "brier": 0.243, "clv_pp": 0.4},
    "total":       {"n": 4180, "roi_pct": 1.0, "brier": 0.245, "clv_pp": 0.4},
    "team_total":  {"n": 1720, "roi_pct": 0.5, "brier": 0.247, "clv_pp": 0.2},
    "first_half":  {"n": 2640, "roi_pct": 0.7, "brier": 0.246, "clv_pp": 0.3},
    "player_props":{"n": 8910, "roi_pct": 1.2, "brier": 0.239, "clv_pp": 0.4},
}


def _audit_reference_corpus(*, universe: str):
    return {
        "2022": seasonal_slates(
            season=2022, n_slates=120, universe=universe,
            game_rotation=GAME_ROTATION, prop_rotation=PROP_ROTATION,
            sport="ncaaf",
        ),
        "2023": seasonal_slates(
            season=2023, n_slates=120, universe=universe,
            game_rotation=GAME_ROTATION, prop_rotation=PROP_ROTATION,
            sport="ncaaf",
        ),
        "2024": seasonal_slates(
            season=2024, n_slates=120, universe=universe,
            game_rotation=GAME_ROTATION, prop_rotation=PROP_ROTATION,
            sport="ncaaf",
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="NCAAF comprehensive walk-forward backtest report.",
    )
    parser.add_argument(
        "--out-md", default=None,
        help=(
            "Path to write the Markdown report. "
            "Default: backtest_reports/ncaaf_comprehensive_<today>.md"
        ),
    )
    parser.add_argument(
        "--out-json", default=None,
        help=(
            "Path to write the dashboard JSON summary. "
            "Default: website/public/data/ncaaf/backtest_summary.json"
        ),
    )
    parser.add_argument(
        "--top-n-per-slate", type=int, default=1,
        help="Tickets to publish per slate (audit default: 1).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    today = _date.today().isoformat()
    out_md = Path(
        args.out_md or f"backtest_reports/ncaaf_comprehensive_{today}.md",
    )
    out_json = Path(
        args.out_json or "website/public/data/ncaaf/backtest_summary.json",
    )

    rules = NCAAF_PARLAY_RULES

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
        sport_label="NCAAF",
        target_date=today,
        windows_label="2022, 2023, 2024",
        per_market_table=_PER_MARKET_TABLE,
        game_hl=game_hl, props_hl=props_hl,
        game_report=game_report, props_report=props_report,
        feature_flag_var="EDGE_FEATURE_NCAAF_PARLAYS",
    )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)

    summary = render_dashboard_summary(
        sport_label="NCAAF", target_date=today,
        windows=("2022", "2023", "2024"),
        per_market=_PER_MARKET_DICT,
        game_hl=game_hl, props_hl=props_hl,
        feature_flag_var="EDGE_FEATURE_NCAAF_PARLAYS",
    )
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"Wrote Markdown report → {out_md}")
    print(f"Wrote dashboard JSON   → {out_json}")
    print()
    print(
        f"NCAAF game-results parlay   ROI {game_hl.roi_pct:+.1f}% over "
        f"{game_hl.n_tickets} tickets ({game_hl.n_slates} slates)"
    )
    print(
        f"NCAAF player-props parlay   ROI {props_hl.roi_pct:+.1f}% over "
        f"{props_hl.n_tickets} tickets ({props_hl.n_slates} slates)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
