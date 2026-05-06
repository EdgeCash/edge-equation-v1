"""Walk-forward backtest for the two MLB parlay engines.

Operates on a corpus of historical per-day leg pools (one entry per
slate). Each entry carries:

* The pool of ``EnrichedLeg``s the per-engine projections produced
  for that date (NRFI/YRFI / full-game / props).
* A mapping ``side_id → realised_outcome`` (True/False) so the
  backtest can settle each generated parlay leg-by-leg.
* The actual closing line (vig-adjusted) so CLV can be measured
  against the line the model fired against.

Produces a ``ParlayBacktestReport`` with:

* ``n_slates`` / ``n_tickets`` — coverage stats.
* ``units_pl`` / ``roi_pct`` — unit P/L on the configured stake.
* ``brier`` — Brier score of the model's joint probability vs the
  realised hit/miss for each ticket.
* ``clv_pp`` — average closing-line value per leg.
* ``hit_rate_buckets`` — calibration table by predicted-prob bucket.
* ``no_qualified_pct`` — share of slates where the engine emitted the
  audit's "No qualified parlay today …" branch.

Two season windows the audit calls out (2023, 2024, 2025) are
supported via the ``windows`` parameter — the report includes a
per-window block plus the all-window aggregate.

This module is intentionally a framework: it doesn't fetch raw data.
The caller (a CI job, a notebook, or the operator's manual run)
provides the corpus from each engine's own backtest harness and the
parlay backtest produces the unified parlay-engine view of the
results. That keeps the parlay engines decoupled from the per-engine
data layers — the same backtest runs against any corpus shape that
satisfies the protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from edge_equation.engines.parlay import (
    ParlayCandidate,
    ParlayConfig,
    build_parlay_candidates,
)
from edge_equation.engines.tiering import Tier
from edge_equation.utils.logging import get_logger

from .game_results_parlay import (
    EnrichedLeg, filter_legs_by_strict_rules as filter_game_results,
)
from .player_props_parlay import (
    filter_legs_by_strict_rules as filter_player_props,
)
from .thresholds import MLB_PARLAY_RULES, MLBParlayRules

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Corpus shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalSlate:
    """One day of historical per-engine outputs.

    ``side_id`` is the unique key for an outcome the corpus settles
    against. We use ``(market_type, game_id, side, line_value)`` for
    full-game and ``(market_type, player_id, side, line_value)`` for
    props — the corpus author picks one convention and uses it for
    every leg + outcome on the slate.
    """

    target_date: str
    legs: tuple[EnrichedLeg, ...]
    outcomes: Mapping[str, bool]
    universe: str = "game_results"   # 'game_results' | 'player_props'

    # Optional CLV per side (delta of vig-adjusted market prob between
    # publish time and close, in percentage points). When absent the
    # backtest treats CLV as 0pp for that leg.
    clv_pp: Mapping[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass
class ParlayBacktestReport:
    """One window's aggregate result + per-window subreports."""

    label: str
    n_slates: int = 0
    n_tickets: int = 0
    n_no_qualified: int = 0
    n_legs_total: int = 0
    units_staked: float = 0.0
    units_pl: float = 0.0
    roi_pct: float = 0.0
    brier: float = 0.0
    avg_joint_prob_corr: float = 0.0
    avg_clv_pp: float = 0.0
    hit_rate_buckets: list[tuple[float, float, int, int]] = field(
        default_factory=list,
    )
    sub_reports: list["ParlayBacktestReport"] = field(default_factory=list)

    @property
    def no_qualified_pct(self) -> float:
        return (
            (self.n_no_qualified / self.n_slates) * 100.0
            if self.n_slates else 0.0
        )

    def summary_text(self) -> str:
        lines = [
            f"PARLAY BACKTEST — {self.label}",
            "─" * 60,
            f"  slates                 {self.n_slates}",
            f"  tickets generated      {self.n_tickets}",
            (
                f"  no-qualified slates    {self.n_no_qualified} "
                f"({self.no_qualified_pct:.1f}%)"
            ),
            f"  total legs             {self.n_legs_total}",
            f"  units staked           {self.units_staked:+.2f}u",
            f"  units P/L              {self.units_pl:+.2f}u",
            f"  ROI                    {self.roi_pct:+.1f}%",
            f"  Brier (joint vs hit)   {self.brier:.4f}",
            f"  avg corr-joint prob    {self.avg_joint_prob_corr*100:.1f}%",
            f"  avg CLV per leg        {self.avg_clv_pp:+.2f}pp",
        ]
        if self.hit_rate_buckets:
            lines.append("")
            lines.append("  Calibration buckets (predicted joint vs realized):")
            lines.append("  " + "─" * 58)
            for lo, hi, n, hits in self.hit_rate_buckets:
                rate = (hits / n * 100.0) if n else 0.0
                lines.append(
                    f"    [{lo*100:>5.1f}-{hi*100:<5.1f}%]  "
                    f"n={n:<4d}  hit={rate:5.1f}%"
                )
        if self.sub_reports:
            lines.append("")
            for sub in self.sub_reports:
                lines.append(sub.summary_text())
                lines.append("")
        return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------


def _settle_candidate(
    cand: ParlayCandidate, outcomes: Mapping[str, bool],
) -> tuple[bool, list[bool]]:
    """Settle one candidate. Returns (all_hit, per_leg_hits).

    A leg whose `side_id` is missing from outcomes is treated as a
    miss — the corpus author should ensure every emitted leg has an
    outcome, but defensive default avoids spurious wins.
    """
    leg_hits: list[bool] = []
    for leg in cand.legs:
        side_id = _side_id_for_leg(leg)
        leg_hits.append(bool(outcomes.get(side_id, False)))
    return all(leg_hits), leg_hits


def _side_id_for_leg(leg) -> str:
    """Same key the corpus author uses for outcomes lookup.

    Convention: ``(market_type|game_or_player_id|side)``. For full-game
    and props that's enough to disambiguate within a slate. Outcomes
    keyed by a different convention will simply miss the lookup and be
    treated as a miss; that's a corpus-author bug, not an engine bug.
    """
    pid_or_gid = leg.player_id or leg.game_id or ""
    return f"{leg.market_type}|{pid_or_gid}|{leg.side}"


def _bucket(p: float) -> tuple[float, float]:
    """Equal-width 10pp bucket the predicted joint probability falls in."""
    edges = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
    for i in range(len(edges) - 1):
        if edges[i] <= p < edges[i + 1]:
            return (edges[i], edges[i + 1])
    return (0.9, 1.0)


def backtest_window(
    slates: Sequence[HistoricalSlate], *,
    label: str,
    universe: str = "game_results",
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    top_n_per_slate: int = 1,
) -> ParlayBacktestReport:
    """Backtest one window (e.g. a single season) on ``slates``.

    Pipeline per slate:
    1. Filter the slate's legs through the strict policy gate.
    2. Hand survivors to the shared parlay builder.
    3. Take the top-``top_n_per_slate`` candidates by EV (audit-spec
       parity: in production the engine publishes 1–3 strict tickets).
    4. Settle each ticket against the slate's outcomes.
    5. Accumulate ROI / Brier / CLV / calibration buckets.
    """
    pcfg = ParlayConfig(
        min_tier=Tier.LEAN,
        max_legs=rules.max_legs,
        default_stake_units=rules.default_stake_units,
        min_joint_prob=rules.min_joint_prob,
        min_ev_units=rules.min_ev_units,
        mc_trials=rules.mc_trials,
        mc_seed=rules.mc_seed,
        max_abs_correlation=rules.max_abs_correlation,
    )

    bucket_counts: dict[tuple[float, float], list[int]] = {}
    n_tickets = 0
    n_no_qualified = 0
    n_legs_total = 0
    units_staked = 0.0
    units_pl = 0.0
    brier_sum = 0.0
    joint_sum = 0.0
    clv_sum = 0.0

    for slate in slates:
        if universe == "game_results":
            qualifying = filter_game_results(
                slate.legs, rules=rules, market_universe="game_results",
            )
        else:
            qualifying = filter_player_props(slate.legs, rules=rules)

        if len(qualifying) < rules.min_legs:
            n_no_qualified += 1
            continue

        plain_legs = [e.leg for e in qualifying]
        candidates = build_parlay_candidates(plain_legs, config=pcfg)
        candidates = [c for c in candidates if c.n_legs >= rules.min_legs]
        if not candidates:
            n_no_qualified += 1
            continue

        for cand in candidates[:top_n_per_slate]:
            all_hit, _ = _settle_candidate(cand, slate.outcomes)
            stake = float(cand.stake_units)
            payout = (cand.combined_decimal_odds - 1.0) * stake
            ticket_pl = payout if all_hit else -stake

            n_tickets += 1
            n_legs_total += cand.n_legs
            units_staked += stake
            units_pl += ticket_pl

            joint = float(cand.joint_prob_corr)
            joint_sum += joint
            outcome = 1.0 if all_hit else 0.0
            brier_sum += (joint - outcome) ** 2

            for leg in cand.legs:
                clv_sum += float(slate.clv_pp.get(_side_id_for_leg(leg), 0.0))

            lo, hi = _bucket(joint)
            counts = bucket_counts.setdefault((lo, hi), [0, 0])
            counts[0] += 1
            counts[1] += int(all_hit)

    n_slates = len(slates)
    avg_joint = joint_sum / n_tickets if n_tickets else 0.0
    avg_brier = brier_sum / n_tickets if n_tickets else 0.0
    avg_clv = clv_sum / n_legs_total if n_legs_total else 0.0
    roi = (units_pl / units_staked * 100.0) if units_staked else 0.0

    buckets_sorted = sorted(bucket_counts.items())
    hit_rate_buckets = [
        (lo, hi, counts[0], counts[1]) for (lo, hi), counts in buckets_sorted
    ]

    return ParlayBacktestReport(
        label=label,
        n_slates=n_slates,
        n_tickets=n_tickets,
        n_no_qualified=n_no_qualified,
        n_legs_total=n_legs_total,
        units_staked=units_staked,
        units_pl=units_pl,
        roi_pct=roi,
        brier=avg_brier,
        avg_joint_prob_corr=avg_joint,
        avg_clv_pp=avg_clv,
        hit_rate_buckets=hit_rate_buckets,
    )


# ---------------------------------------------------------------------------
# Walk-forward orchestrator
# ---------------------------------------------------------------------------


def walk_forward_backtest(
    *,
    windows: Mapping[str, Sequence[HistoricalSlate]],
    universe: str = "game_results",
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    top_n_per_slate: int = 1,
) -> ParlayBacktestReport:
    """Run a walk-forward backtest over ``windows`` (e.g. seasons).

    ``windows`` is a mapping ``label -> slates``. The function returns
    one aggregate ``ParlayBacktestReport`` whose ``sub_reports`` carry
    the per-window subreports — that's the format the audit's
    "comprehensive backtest report" calls out (cumulative + per-season
    splits).
    """
    sub_reports: list[ParlayBacktestReport] = []
    all_slates: list[HistoricalSlate] = []
    for label, slates in windows.items():
        sub = backtest_window(
            slates, label=label, universe=universe,
            rules=rules, top_n_per_slate=top_n_per_slate,
        )
        sub_reports.append(sub)
        all_slates.extend(slates)
    aggregate = backtest_window(
        all_slates,
        label=f"AGGREGATE ({len(windows)} windows, "
              f"{len(all_slates)} slates)",
        universe=universe, rules=rules, top_n_per_slate=top_n_per_slate,
    )
    aggregate.sub_reports = sub_reports
    return aggregate


# ---------------------------------------------------------------------------
# Comprehensive report builder
# ---------------------------------------------------------------------------


@dataclass
class ComprehensiveBacktestReport:
    """The full audit report covering BOTH parlay engines + each
    underlying market's own backtest summary line.

    The two parlay engines come with their walk-forward reports;
    per-market summaries are pulled in as plain text from each
    engine's existing backtest harness so the operator gets one
    document instead of three.
    """

    target_window_label: str
    game_results_parlay: Optional[ParlayBacktestReport] = None
    player_props_parlay: Optional[ParlayBacktestReport] = None
    per_market_summaries: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        sections: list[str] = [
            f"MLB COMPREHENSIVE BACKTEST — {self.target_window_label}",
            "=" * 60,
            "",
            "Per-market summaries (engine-owned backtests):",
            "─" * 60,
        ]
        if self.per_market_summaries:
            for market, text in self.per_market_summaries.items():
                sections.append(f"\n[{market}]")
                sections.append(text.rstrip())
        else:
            sections.append("  (no per-market summaries provided to this run)")

        sections.append("")
        sections.append("Strict-parlay walk-forward results:")
        sections.append("─" * 60)
        if self.game_results_parlay is not None:
            sections.append("")
            sections.append(self.game_results_parlay.summary_text())
        if self.player_props_parlay is not None:
            sections.append("")
            sections.append(self.player_props_parlay.summary_text())

        sections.append("")
        sections.append(
            "Notes: Strict thresholds — min legs 3, max legs 6, "
            "≥4pp edge OR ELITE per leg, EV>0 after vig. "
            "No-qualified slates produce the audit's "
            "'No qualified parlay today …' branch."
        )
        return "\n".join(sections).rstrip() + "\n"


def build_comprehensive_report(
    *,
    windows: Mapping[str, Sequence[HistoricalSlate]],
    props_windows: Optional[Mapping[str, Sequence[HistoricalSlate]]] = None,
    per_market_summaries: Optional[dict[str, str]] = None,
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    top_n_per_slate: int = 1,
) -> ComprehensiveBacktestReport:
    """Build the unified backtest report.

    ``windows`` carries the game-results parlay corpus.
    ``props_windows`` (defaulting to ``windows``) carries the
    player-props parlay corpus — they're separate because the
    per-engine backtests usually emit different leg shapes / outcome
    keys for each universe.
    """
    game_report = walk_forward_backtest(
        windows=windows, universe="game_results",
        rules=rules, top_n_per_slate=top_n_per_slate,
    )
    props_report = walk_forward_backtest(
        windows=props_windows or windows, universe="player_props",
        rules=rules, top_n_per_slate=top_n_per_slate,
    )
    label = f"{', '.join(windows.keys())}"
    return ComprehensiveBacktestReport(
        target_window_label=label,
        game_results_parlay=game_report,
        player_props_parlay=props_report,
        per_market_summaries=per_market_summaries or {},
    )
