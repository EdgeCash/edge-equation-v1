"""Feature A/B diff utility for the NRFI v2 rollout.

Used pre-retrain to inspect what the new F1 features (pitch-mix,
F-strike, ump F1, opener flag, Woolner prior) look like on real
slates without committing to a full model retrain. Three things it
surfaces:

  1. Which columns are NEW vs. the previously-trained model's
     expected feature set.
  2. Population summary stats per new column (mean / std / pct of
     non-default rows / sample distribution) so we catch
     all-defaults issues before they sink the retrain.
  3. Diffs between two feature builds on the same slate --- e.g.
     ``NRFI_DISABLE_F1_V2=1`` vs default --- so the operator can
     verify the new features actually move the column space.

This module is read-only --- it doesn't write features anywhere or
trigger training. The operator runs it as a sanity check, then
kicks off ``nrfi-walkforward-train.yml`` if the diff looks sane.
"""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional


# Stable list of the new columns this PR introduces. Used to verify
# they actually appear in the built feature row + to summarize them
# in isolation. If a new column is added in a future PR, append it
# here (or move to a manifest file when the list grows).
NEW_F1_V2_COLUMNS: tuple[str, ...] = (
    # Pitcher arsenal F1 mix + F-strike (per side)
    "home_p_f1_mix_ff", "home_p_f1_mix_si", "home_p_f1_mix_fc",
    "home_p_f1_mix_cu", "home_p_f1_mix_ch", "home_p_f1_arsenal_depth",
    "home_p_f_strike_pct", "home_p_f_strike_sample",
    "home_p_is_opener",
    "away_p_f1_mix_ff", "away_p_f1_mix_si", "away_p_f1_mix_fc",
    "away_p_f1_mix_cu", "away_p_f1_mix_ch", "away_p_f1_arsenal_depth",
    "away_p_f_strike_pct", "away_p_f_strike_sample",
    "away_p_is_opener",
    # F1 split shrinkage (raw companions kept alongside)
    "home_p_first_inn_k_pct_raw", "home_p_first_inn_bb_pct_raw",
    "away_p_first_inn_k_pct_raw", "away_p_first_inn_bb_pct_raw",
    # Umpire F1
    "ump_f1_csa", "ump_f1_csa_raw", "ump_f1_walk_rate",
    "ump_f1_called_sample", "ump_f1_pa_sample",
    # Interactions
    "int_home_p_f_strike_x_ump_f1_csa",
    "int_away_p_f_strike_x_ump_f1_csa",
    "int_home_p_arsenal_x_top3_k",
    "int_away_p_arsenal_x_top3_k",
    "int_home_p_opener_x_kpct",
    "int_away_p_opener_x_kpct",
    # Woolner calibration prior
    "woolner_top_rpg", "woolner_bottom_rpg", "woolner_nrfi_prior",
)


# Env var that short-circuits the v2 wiring for A/B testing without a
# code change. When set to ``1`` / ``true`` / ``yes``, the backtest
# helpers return empty dicts / default arsenals / False openers so
# the model sees the pre-v2 feature distribution.
ENV_DISABLE_V2 = "NRFI_DISABLE_F1_V2"


def v2_features_disabled() -> bool:
    """Resolve the disable flag once per call site --- avoids
    importing this module just to read os.environ."""
    raw = os.environ.get(ENV_DISABLE_V2, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class ColumnSummary:
    """Per-column diagnostic surfaced in the diff report."""
    column: str
    n: int = 0
    n_nonzero: int = 0
    mean: float = 0.0
    std: float = 0.0
    min: float = 0.0
    max: float = 0.0
    n_default: int = 0    # rows whose value matches the documented default

    def fraction_nonzero(self) -> float:
        return self.n_nonzero / self.n if self.n > 0 else 0.0

    def fraction_default(self) -> float:
        return self.n_default / self.n if self.n > 0 else 1.0


@dataclass
class FeatureDiffReport:
    """Full diff: which v2 columns landed, which are all-defaults, and
    distribution stats per column. Renders to plain text for
    operator-friendly stdout."""
    columns_present: list[str] = field(default_factory=list)
    columns_missing: list[str] = field(default_factory=list)
    summaries: list[ColumnSummary] = field(default_factory=list)
    n_rows: int = 0


# Default values per column --- used to count "default" rows for the
# fraction_default sanity stat. Anything not listed here is checked
# against 0.0 (a reasonable fallback for interactions / sample sizes).
_COLUMN_DEFAULTS: dict[str, float] = {
    "home_p_f1_mix_ff": 0.40, "away_p_f1_mix_ff": 0.40,
    "home_p_f1_mix_si": 0.18, "away_p_f1_mix_si": 0.18,
    "home_p_f1_mix_fc": 0.07, "away_p_f1_mix_fc": 0.07,
    "home_p_f1_mix_cu": 0.10, "away_p_f1_mix_cu": 0.10,
    "home_p_f1_mix_ch": 0.13, "away_p_f1_mix_ch": 0.13,
    "home_p_f1_arsenal_depth": 4.0, "away_p_f1_arsenal_depth": 4.0,
    "home_p_f_strike_pct": 0.62, "away_p_f_strike_pct": 0.62,
    "ump_f1_walk_rate": 0.085,
}


def summarize_columns(
    rows: Iterable[Mapping[str, float]],
    columns: Iterable[str] = NEW_F1_V2_COLUMNS,
    *,
    default_tolerance: float = 1e-6,
) -> FeatureDiffReport:
    """Walk a sequence of feature rows and emit a per-column diagnostic.

    Use to verify a new feature pipeline actually populates the
    target columns with non-degenerate values before retraining. A
    column whose ``fraction_default >= 0.95`` likely means the
    upstream Statcast pull isn't reaching the aggregator --- worth
    investigating before kicking off the retrain.
    """
    rows_list = list(rows)
    cols = list(columns)
    report = FeatureDiffReport(n_rows=len(rows_list))

    if not rows_list:
        report.columns_missing = cols
        return report

    # First pass: which columns appear in *any* row?
    seen: set[str] = set()
    for r in rows_list:
        seen.update(r.keys())
    report.columns_present = [c for c in cols if c in seen]
    report.columns_missing = [c for c in cols if c not in seen]

    # Second pass: per-column summaries.
    for col in report.columns_present:
        vals = [float(r[col]) for r in rows_list if col in r]
        if not vals:
            continue
        default = _COLUMN_DEFAULTS.get(col, 0.0)
        nonzero = sum(1 for v in vals if abs(v) > default_tolerance)
        n_default = sum(1 for v in vals if abs(v - default) < default_tolerance)
        summary = ColumnSummary(
            column=col,
            n=len(vals),
            n_nonzero=nonzero,
            mean=statistics.fmean(vals),
            std=statistics.pstdev(vals) if len(vals) >= 2 else 0.0,
            min=min(vals),
            max=max(vals),
            n_default=n_default,
        )
        report.summaries.append(summary)

    return report


def render_report(report: FeatureDiffReport) -> str:
    """Plain-text rendering for operator stdout.

    Highlights missing columns + columns where >= 95% of rows hold the
    documented default value (likely indicates Statcast wiring issue).
    """
    out: list[str] = []
    out.append(
        f"NRFI v2 feature diff --- {report.n_rows} feature rows examined"
    )
    out.append("=" * 60)
    if report.columns_missing:
        out.append("")
        out.append(f"MISSING ({len(report.columns_missing)}):")
        for c in report.columns_missing:
            out.append(f"  ! {c}")

    out.append("")
    out.append(f"PRESENT ({len(report.columns_present)}):")
    out.append(
        f"  {'column':<42} {'n':>6} {'mean':>9} {'std':>9} "
        f"{'%default':>9} {'%nonzero':>9}"
    )
    out.append("  " + "-" * 90)
    for s in report.summaries:
        flag = "!! " if s.fraction_default() >= 0.95 else "   "
        out.append(
            f"  {flag}{s.column:<39} {s.n:>6} {s.mean:>9.4f} "
            f"{s.std:>9.4f} {s.fraction_default()*100:>8.1f}% "
            f"{s.fraction_nonzero()*100:>8.1f}%"
        )

    suspicious = [s for s in report.summaries if s.fraction_default() >= 0.95]
    if suspicious:
        out.append("")
        out.append(
            f"WARNING: {len(suspicious)} columns are >=95% default values. "
            f"Likely the upstream data pull isn't populating them yet.",
        )
    return "\n".join(out)
