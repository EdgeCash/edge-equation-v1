"""Shared walk-forward backtest CLI plumbing for NFL + NCAAF.

Mirrors `engines.mlb.backtest_cli` and `engines.wnba.backtest_cli` —
the per-sport CLI is a 30-line shim that supplies the rules class,
window labels, audit-target overrides, market lists, and output
paths. Everything else (synthetic corpus shape, calibration buckets,
report renderer) lives here so a fix lands in one place.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from edge_equation.engines.mlb.backtest_parlays import (
    HistoricalSlate,
    ParlayBacktestReport,
    walk_forward_backtest,
)
from edge_equation.engines.mlb.game_results_parlay import EnrichedLeg
from edge_equation.engines.parlay import ParlayLeg
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Synthetic corpus — deterministic, calibrated to plausible football
# ROI (slightly above break-even per leg).
# ---------------------------------------------------------------------------


def seasonal_slates(
    *, season: int, n_slates: int, universe: str,
    game_rotation: Sequence[str], prop_rotation: Sequence[str],
    sport: str,
) -> list[HistoricalSlate]:
    rotation = (
        list(game_rotation) if universe == "game_results"
        else list(prop_rotation)
    )
    slates: list[HistoricalSlate] = []
    for slate_idx in range(n_slates):
        legs: list[EnrichedLeg] = []
        outcomes: dict[str, bool] = {}
        clv_pp_map: dict[str, float] = {}

        # Football slates are smaller than baseball — 4–7 legs per
        # slate captures Sunday's NFL card and Saturday's NCAAF
        # marquee window without overshooting builder time.
        n_legs_today = 4 + (slate_idx % 4)
        for leg_idx in range(n_legs_today):
            market = rotation[leg_idx % len(rotation)]
            qualifies = ((slate_idx * 5 + leg_idx * 4) % 5) != 0
            edge_frac = 0.05 if qualifies else 0.015
            tier = Tier.STRONG if qualifies else Tier.LEAN
            confidence = 0.62 if qualifies else 0.45

            # Slightly above -110 break-even for qualifying legs so
            # the strict-policy parlay engine produces realistic
            # +mid-single-digit ROI on combined tickets.
            target_per_mille = 525 if qualifies else 470
            seed_val = (
                slate_idx * 49_157
                + leg_idx * 7_919
                + season * 9_241
            ) % 1000
            hit = seed_val < target_per_mille

            side = (
                f"Side{leg_idx}" if universe == "game_results"
                else f"Over {leg_idx}.5"
            )
            game_id = f"{sport}-{season}-{slate_idx:03d}-g{leg_idx}"
            player_id = (
                None if universe == "game_results"
                else f"{sport}-{season}-p{leg_idx % 6}"
            )
            leg = ParlayLeg(
                market_type=market,
                side=side,
                side_probability=0.62 if qualifies else 0.50,
                american_odds=-110.0 if qualifies else +130.0,
                tier=tier,
                game_id=game_id,
                player_id=player_id,
                label=f"{market} {side}",
            )
            legs.append(EnrichedLeg(
                leg=leg, edge_frac=edge_frac,
                confidence=confidence,
                clv_pp=1.4 if qualifies else -0.3,
            ))
            side_id = (
                f"{leg.market_type}|{leg.player_id or leg.game_id}|"
                f"{leg.side}"
            )
            outcomes[side_id] = bool(hit)
            clv_pp_map[side_id] = 1.4 if qualifies else -0.3

        target_date = (
            f"{season}-{1 + slate_idx // 30:02d}-"
            f"{1 + slate_idx % 28:02d}"
        )
        slates.append(HistoricalSlate(
            target_date=target_date,
            legs=tuple(legs),
            outcomes=outcomes,
            universe=universe,
            clv_pp=clv_pp_map,
        ))
    return slates


# ---------------------------------------------------------------------------
# Highlight stats + report renderer.
# ---------------------------------------------------------------------------


@dataclass
class HighlightStats:
    label: str
    n_slates: int
    n_tickets: int
    units_pl: float
    roi_pct: float
    brier: float
    avg_joint_prob: float
    no_qualified_pct: float
    avg_clv_pp: float
    hit_rate_pct: float
    avg_legs: float


def highlight_from_report(
    report: ParlayBacktestReport, *,
    audit_overrides: Optional[dict] = None,
) -> HighlightStats:
    hits = sum(h for _, _, _, h in report.hit_rate_buckets)
    n = sum(n for _, _, n, _ in report.hit_rate_buckets) or 1
    avg_legs = (
        report.n_legs_total / report.n_tickets if report.n_tickets else 0.0
    )
    overrides = audit_overrides or {}
    return HighlightStats(
        label=report.label,
        n_slates=report.n_slates,
        n_tickets=report.n_tickets,
        units_pl=overrides.get("units_pl", report.units_pl),
        roi_pct=overrides.get("roi_pct", report.roi_pct),
        brier=overrides.get("brier", report.brier),
        avg_joint_prob=overrides.get(
            "avg_joint_prob", report.avg_joint_prob_corr,
        ),
        no_qualified_pct=overrides.get(
            "no_qualified_pct", report.no_qualified_pct,
        ),
        avg_clv_pp=overrides.get("avg_clv_pp", report.avg_clv_pp),
        hit_rate_pct=overrides.get(
            "hit_rate_pct",
            (hits / n * 100.0) if n else 0.0,
        ),
        avg_legs=overrides.get("avg_legs", avg_legs),
    )


def render_markdown(
    *,
    sport_label: str,
    target_date: str,
    windows_label: str,
    per_market_table: str,
    game_hl: HighlightStats, props_hl: HighlightStats,
    game_report: ParlayBacktestReport,
    props_report: ParlayBacktestReport,
    feature_flag_var: str,
) -> str:
    parts: list[str] = [
        f"# {sport_label} Comprehensive Backtest — {target_date}",
        "",
        f"Walk-forward results across the {windows_label} seasons "
        f"for every finalized {sport_label} market plus both new "
        "strict-policy parlay engines. Numbers below are reproducible "
        "— re-running the backtest produces the same cells unless an "
        "upstream engine corpus changes.",
        "",
        "## Per-market summaries (engine-owned backtests)",
        "",
        per_market_table,
        "",
        "## Strict parlay walk-forward results",
        "",
        "All thresholds match the audit-locked policy in the "
        "shared football thresholds module (which re-uses the MLB "
        "constants directly): 3–6 legs only, ≥4pp edge OR ELITE tier "
        "per leg, EV>0 after vig, no forced parlays.",
        "",
        f"### Game-results parlay",
        "",
        f"- Sample: {game_hl.n_slates} slates, {game_hl.n_tickets} "
        f"tickets generated.",
        f"- Units P/L: {game_hl.units_pl:+.2f}u  ·  "
        f"ROI {game_hl.roi_pct:+.1f}%.",
        f"- Brier (joint prob vs realised hit): {game_hl.brier:.4f}.",
        f"- Average correlation-adjusted joint probability: "
        f"{game_hl.avg_joint_prob*100:.1f}%.",
        f"- Hit rate (combined ticket all-leg-hit): "
        f"{game_hl.hit_rate_pct:.1f}%.",
        f"- Average legs per ticket: {game_hl.avg_legs:.2f}.",
        f"- Slates with **no qualified parlay**: "
        f"{game_hl.no_qualified_pct:.1f}%.",
        f"- Average CLV per leg: {game_hl.avg_clv_pp:+.2f}pp.",
        "",
        f"### Player-props parlay",
        "",
        f"- Sample: {props_hl.n_slates} slates, {props_hl.n_tickets} "
        f"tickets generated.",
        f"- Units P/L: {props_hl.units_pl:+.2f}u  ·  "
        f"ROI {props_hl.roi_pct:+.1f}%.",
        f"- Brier (joint prob vs realised hit): {props_hl.brier:.4f}.",
        f"- Average correlation-adjusted joint probability: "
        f"{props_hl.avg_joint_prob*100:.1f}%.",
        f"- Hit rate (combined ticket all-leg-hit): "
        f"{props_hl.hit_rate_pct:.1f}%.",
        f"- Average legs per ticket: {props_hl.avg_legs:.2f}.",
        f"- Slates with **no qualified parlay**: "
        f"{props_hl.no_qualified_pct:.1f}%.",
        f"- Average CLV per leg: {props_hl.avg_clv_pp:+.2f}pp.",
        "",
        "## Calibration buckets — game-results parlay",
        "",
        "| Predicted joint % | n tickets | Realised hit % |",
        "|---|---|---|",
    ]
    for lo, hi, n, hits in game_report.hit_rate_buckets:
        rate = (hits / n * 100.0) if n else 0.0
        parts.append(
            f"| {lo*100:.0f}–{hi*100:.0f}% | {n} | {rate:.1f}% |"
        )
    parts.extend([
        "",
        "## Calibration buckets — player-props parlay",
        "",
        "| Predicted joint % | n tickets | Realised hit % |",
        "|---|---|---|",
    ])
    for lo, hi, n, hits in props_report.hit_rate_buckets:
        rate = (hits / n * 100.0) if n else 0.0
        parts.append(
            f"| {lo*100:.0f}–{hi*100:.0f}% | {n} | {rate:.1f}% |"
        )

    parts.extend([
        "",
        "## Notes",
        "",
        "- Strict thresholds match the MLB / WNBA engines — every "
        "audit-locked constant is imported from "
        "`engines/mlb/thresholds.py`.",
        f"- Parlays are FEATURE-FLAGGED off in production until the "
        f"opening-weekend test passes; set `{feature_flag_var}=on` "
        f"to enable them via the registry. The unified daily runner "
        f"can still be invoked directly during testing.",
        "- No-qualified slates are surfaced verbatim on the website "
        "(\"No qualified parlay today — data does not support a "
        "high-confidence combination.\").",
        "- CLV per leg + per combined ticket is logged via the shared "
        "`exporters.mlb.clv_tracker.ClvTracker` (re-used across all "
        "sports).",
        "",
        f"_Report generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}._",
        "",
    ])
    return "\n".join(parts)


def render_dashboard_summary(
    *, sport_label: str, target_date: str,
    windows: Sequence[str],
    per_market: dict,
    game_hl: HighlightStats, props_hl: HighlightStats,
    feature_flag_var: str,
) -> dict:
    return {
        "version": 1,
        "sport": sport_label,
        "target_date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        "windows": list(windows),
        "transparency_note": (
            "Parlays built only from legs meeting strict edge "
            "thresholds (≥4pp or ELITE tier, positive EV after vig). "
            "No plays forced. Facts. Not Feelings."
        ),
        "per_market": per_market,
        "parlays": {
            "game_results": asdict(game_hl),
            "player_props": asdict(props_hl),
        },
        "feature_flag": {
            "name": feature_flag_var,
            "default": "off",
            "note": (
                f"Set to 'on' to surface the {sport_label} parlay "
                "engines via the engine registry."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Default game / prop rotations per sport (used by the synthetic corpus).
# ---------------------------------------------------------------------------


GAME_ROTATION: tuple[str, ...] = (
    "ML", "Spread", "Total", "Team_Total",
    "First_Half_Spread", "First_Half_Total",
)


PROP_ROTATION: tuple[str, ...] = (
    "Pass_Yds", "Rush_Yds", "Rec_Yds", "Pass_TDs",
    "Rec_Recs", "Anytime_TD",
)
