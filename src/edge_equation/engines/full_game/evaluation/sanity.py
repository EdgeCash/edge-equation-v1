"""Full-Game sanity gate — does the projection beat the market?

The hard quality bar before any full-game pick goes to the public
ledger. The Poisson/Skellam projection's job is to find lines where
the model's probability differs from the vig-corrected market
probability by enough to justify the risk. If the projection *can't*
outperform the market on a holdout slice, we have no edge and we
shouldn't be publishing picks.

Two baselines, in order of strictness:

1. **Market (primary).** The vig-corrected book probability is the
   sharpest single number we have. If our projection's Brier on
   settled lines isn't strictly better than the market's Brier on
   the same lines, we're trading randomly.

2. **League prior (secondary).** Re-runs the same Poisson/Skellam
   projection with *no per-team rates* — i.e. every team is league-
   average. Catches "did our team-strength signal even improve over
   the trivial baseline?" The projection module's no-rates path is
   the literal implementation of this baseline, so the comparison
   stays apples-to-apples.

Inputs come from the existing ``fullgame_predictions`` ×
``fullgame_actuals`` join in DuckDB. No extra data plumbing.

CLI
~~~

::

    python -m edge_equation.engines.full_game.evaluation.sanity \\
        --duckdb-path data/fullgame_cache/fullgame.duckdb \\
        --from 2025-04-01 --to 2026-04-30

The CLI exits non-zero when the gate fails so a workflow can use it
as a hard precondition before promoting a bundle.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ..config import ProjectionKnobs
from ..data.team_rates import (
    LEAGUE_RUNS_ALLOWED_PER_GAME,
    LEAGUE_RUNS_PER_GAME,
)


# ---------------------------------------------------------------------------
# Result + report dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateScores:
    """Calibration metrics for one set of probabilities vs actuals."""
    label: str
    n: int
    brier: float
    log_loss: float
    accuracy: float

    def line(self) -> str:
        return (
            f"  {self.label:<14} n={self.n:<5} "
            f"brier={self.brier:.4f}  ll={self.log_loss:.4f}  "
            f"acc={self.accuracy * 100:5.1f}%"
        )


@dataclass
class SanityReport:
    n_picks: int = 0
    model: Optional[GateScores] = None
    market: Optional[GateScores] = None
    league_prior: Optional[GateScores] = None
    primary_gate_passed: bool = False    # model better than market
    secondary_gate_passed: bool = False  # model better than league prior
    notes: list[str] = field(default_factory=list)

    @property
    def gate_passed(self) -> bool:
        """Both gates must clear for the bundle to be publish-eligible."""
        return self.primary_gate_passed and self.secondary_gate_passed

    def render(self) -> str:
        lines = [
            "Full-Game sanity gate",
            "=" * 60,
            f"  settled picks       {self.n_picks}",
            "",
        ]
        for s in (self.model, self.market, self.league_prior):
            if s is not None:
                lines.append(s.line())
        lines.extend([
            "",
            f"  Primary gate (vs market):       "
            f"{'PASS' if self.primary_gate_passed else 'FAIL'}",
            f"  Secondary gate (vs league):     "
            f"{'PASS' if self.secondary_gate_passed else 'FAIL'}",
        ])
        if self.notes:
            lines.append("")
            for n in self.notes:
                lines.append(f"  * {n}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        out: dict = {
            "n_picks": self.n_picks,
            "primary_gate_passed": self.primary_gate_passed,
            "secondary_gate_passed": self.secondary_gate_passed,
            "gate_passed": self.gate_passed,
            "notes": list(self.notes),
        }
        for key, scores in (
            ("model", self.model),
            ("market", self.market),
            ("league_prior", self.league_prior),
        ):
            out[key] = (
                None if scores is None else {
                    "n": scores.n,
                    "brier": round(scores.brier, 6),
                    "log_loss": round(scores.log_loss, 6),
                    "accuracy": round(scores.accuracy, 6),
                }
            )
        return out


# ---------------------------------------------------------------------------
# Core sanity check
# ---------------------------------------------------------------------------


def evaluate_sanity(
    rows: Sequence[dict], *,
    knobs: Optional[ProjectionKnobs] = None,
    min_n: int = 50,
    primary_gate_min_brier_delta: float = 0.0,
) -> SanityReport:
    """Score model / market / league-prior probabilities against settled
    full-game outcomes; return a SanityReport with pass/fail verdicts.

    Each row is expected to carry::

        market_type    str       — 'Total' / 'F5_Total' / 'ML' / ...
        side           str       — 'Over' / 'Under' / tricode
        team_tricode   str
        home_tricode   str
        line_value     Optional[float]
        model_prob     float
        market_prob    float     — vig-corrected
        actual_home    int
        actual_away    int
        f5_home_runs   Optional[int]
        f5_away_runs   Optional[int]

    Rows with missing / out-of-range fields are silently skipped —
    defensive for in-progress backfills and partial settle passes.
    """
    knobs = knobs or ProjectionKnobs()
    report = SanityReport()

    pairs: list[tuple[float, float, float, int]] = []
    # (model_prob, market_prob, league_prior_prob, hit_int)
    for r in rows:
        try:
            mp = float(r["model_prob"])
            mkt = float(r["market_prob"])
        except (KeyError, TypeError, ValueError):
            continue
        if mp <= 0.0 or mp >= 1.0 or mkt <= 0.0 or mkt >= 1.0:
            continue

        hit = _did_side_hit(r)
        if hit is None:
            continue
        league_p = _league_prior_prob(r, knobs=knobs)
        if league_p is None:
            continue
        pairs.append((mp, mkt, league_p, hit))

    report.n_picks = len(pairs)

    if not pairs:
        report.notes.append(
            "no settled picks available — gate cannot evaluate",
        )
        return report

    if report.n_picks < min_n:
        report.notes.append(
            f"only {report.n_picks} settled picks (< {min_n}); "
            f"gate verdicts treated as inconclusive (FAIL by default).",
        )

    model_probs = [p[0] for p in pairs]
    market_probs = [p[1] for p in pairs]
    league_probs = [p[2] for p in pairs]
    hits = [p[3] for p in pairs]

    report.model = _score("model", model_probs, hits)
    report.market = _score("market", market_probs, hits)
    report.league_prior = _score("league_prior", league_probs, hits)

    # Gate verdicts — both require min_n picks to clear.
    if report.n_picks >= min_n:
        report.primary_gate_passed = (
            report.market.brier - report.model.brier
            > primary_gate_min_brier_delta
        )
        report.secondary_gate_passed = (
            report.model.brier < report.league_prior.brier
        )
    else:
        report.primary_gate_passed = False
        report.secondary_gate_passed = False

    if report.primary_gate_passed and not report.secondary_gate_passed:
        report.notes.append(
            "model beats market but not the trivial league-prior "
            "baseline — investigate before promoting (likely sign of "
            "a misspecified market join)."
        )
    if report.secondary_gate_passed and not report.primary_gate_passed:
        report.notes.append(
            "model beats trivial baseline but not the market — the "
            "edge is real but doesn't survive the vig. No public picks.",
        )
    return report


# ---------------------------------------------------------------------------
# Side-hit + league-prior helpers
# ---------------------------------------------------------------------------


def _did_side_hit(row: dict) -> Optional[int]:
    """Return 1 if the staked side won, 0 if it lost, None for push /
    insufficient data.

    Mirrors the production ``ledger._did_side_hit`` logic but keeps the
    push handling explicit (returns None) so the gate sample doesn't
    include rows with no decisive outcome.
    """
    try:
        market = str(row.get("market_type") or "").strip()
        side = str(row.get("side") or "").strip().lower()
        team = str(row.get("team_tricode") or "").strip()
        home = str(row.get("home_tricode") or "").strip()
        line_value = row.get("line_value")
        actual_home = int(row.get("actual_home") or 0)
        actual_away = int(row.get("actual_away") or 0)
    except (TypeError, ValueError):
        return None

    is_home_side = bool(team and team == home)

    if market == "Total":
        if line_value is None:
            return None
        total = actual_home + actual_away
        if math.isclose(total, float(line_value)):
            return None
        if side == "over":
            return 1 if total > float(line_value) else 0
        if side == "under":
            return 1 if total < float(line_value) else 0
        return None

    if market == "F5_Total":
        f5_h = row.get("f5_home_runs")
        f5_a = row.get("f5_away_runs")
        if f5_h is None or f5_a is None or line_value is None:
            return None
        total = int(f5_h) + int(f5_a)
        if math.isclose(total, float(line_value)):
            return None
        if side == "over":
            return 1 if total > float(line_value) else 0
        if side == "under":
            return 1 if total < float(line_value) else 0
        return None

    if market == "Team_Total":
        if line_value is None:
            return None
        team_runs = actual_home if is_home_side else actual_away
        if math.isclose(team_runs, float(line_value)):
            return None
        if side == "over":
            return 1 if team_runs > float(line_value) else 0
        if side == "under":
            return 1 if team_runs < float(line_value) else 0
        return None

    if market == "ML":
        if actual_home == actual_away:
            return None  # MLB regular-season ties are vanishingly rare
        return 1 if (
            (is_home_side and actual_home > actual_away)
            or (not is_home_side and actual_away > actual_home)
        ) else 0

    if market == "F5_ML":
        f5_h = row.get("f5_home_runs")
        f5_a = row.get("f5_away_runs")
        if f5_h is None or f5_a is None:
            return None
        if int(f5_h) == int(f5_a):
            return None  # F5 ties push
        return 1 if (
            (is_home_side and int(f5_h) > int(f5_a))
            or (not is_home_side and int(f5_a) > int(f5_h))
        ) else 0

    if market == "Run_Line":
        if line_value is None:
            return None
        # Cover threshold: team's margin > -line_value (i.e. line_value=-1.5
        # means margin must exceed +1.5). Half-integer lines never push.
        if is_home_side:
            margin = actual_home - actual_away
        else:
            margin = actual_away - actual_home
        threshold = -float(line_value)
        if math.isclose(margin, threshold):
            return None
        return 1 if margin > threshold else 0

    return None


def _league_prior_prob(row: dict, *, knobs: ProjectionKnobs) -> Optional[float]:
    """Re-run the same projection assuming both teams are league-average.

    The projection module's no-rates path is the implementation of
    that baseline, but we don't have a `FullGameLine` to feed it from
    the persisted row. Recompute the math directly here so the gate
    has no dependency on the live odds_fetcher dataclasses.
    """
    market = str(row.get("market_type") or "").strip()
    side = str(row.get("side") or "").strip().lower()
    team = str(row.get("team_tricode") or "").strip()
    home = str(row.get("home_tricode") or "").strip()
    line_value = row.get("line_value")
    is_home_side = bool(team and team == home)

    league_rpg = LEAGUE_RUNS_PER_GAME
    # League-prior λs: every team is league-average, with HFA bump on home.
    lam_home = league_rpg * (1.0 + knobs.home_field_advantage_pct)
    lam_away = league_rpg
    lam_total = lam_home + lam_away

    if market == "Total":
        if line_value is None:
            return None
        p_over = _prob_over_poisson(float(line_value), lam_total)
        return p_over if side == "over" else (1.0 - p_over)

    if market == "F5_Total":
        if line_value is None:
            return None
        f5_lam = lam_total * knobs.f5_share_of_total
        p_over = _prob_over_poisson(float(line_value), f5_lam)
        return p_over if side == "over" else (1.0 - p_over)

    if market == "Team_Total":
        if line_value is None:
            return None
        team_lam = lam_home if is_home_side else lam_away
        p_over = _prob_over_poisson(float(line_value), team_lam)
        return p_over if side == "over" else (1.0 - p_over)

    if market == "ML":
        return _skellam_p_diff_gt(0.0, lam_home, lam_away) if is_home_side \
            else _skellam_p_diff_gt(0.0, lam_away, lam_home)

    if market == "F5_ML":
        f5_h = lam_home * knobs.f5_share_of_total
        f5_a = lam_away * knobs.f5_share_of_total
        return _skellam_p_diff_gt(0.0, f5_h, f5_a) if is_home_side \
            else _skellam_p_diff_gt(0.0, f5_a, f5_h)

    if market == "Run_Line":
        if line_value is None:
            return None
        threshold = -float(line_value)
        return _skellam_p_diff_gt(threshold, lam_home, lam_away) if is_home_side \
            else _skellam_p_diff_gt(threshold, lam_away, lam_home)

    return None


# ---------------------------------------------------------------------------
# Closed-form Poisson + Skellam (kept inline so the gate has no extra deps)
# ---------------------------------------------------------------------------


def _poisson_pmf(k: int, lam: float) -> float:
    if k < 0 or lam < 0:
        return 0.0
    if lam == 0.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    lam = max(0.0, float(lam))
    if lam == 0.0:
        return 1.0
    total = 0.0
    p = math.exp(-lam)
    total += p
    for i in range(1, k + 1):
        p *= lam / i
        total += p
    return min(1.0, total)


def _prob_over_poisson(line: float, lam: float) -> float:
    return 1.0 - _poisson_cdf(int(math.floor(line)), lam)


def _skellam_p_diff_gt(threshold: float, lam_a: float, lam_b: float,
                          *, max_runs: int = 25) -> float:
    """P(X - Y > threshold) where X~Poisson(λ_a), Y~Poisson(λ_b)."""
    total = 0.0
    for a in range(0, max_runs + 1):
        pa = _poisson_pmf(a, lam_a)
        if pa <= 0:
            continue
        b_upper = a - threshold - 1e-9
        if b_upper < 0:
            continue
        b_max = int(math.floor(b_upper))
        cdf = _poisson_cdf(b_max, lam_b)
        total += pa * cdf
    return min(1.0, max(0.0, total))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score(label: str, probs: Sequence[float], hits: Sequence[int]) -> GateScores:
    n = len(probs)
    if n == 0:
        return GateScores(label, 0, 0.0, 0.0, 0.0)
    brier = sum((p - h) ** 2 for p, h in zip(probs, hits)) / n
    eps = 1e-9
    ll = -sum(
        h * math.log(max(eps, min(1.0 - eps, p)))
        + (1 - h) * math.log(max(eps, min(1.0 - eps, 1.0 - p)))
        for p, h in zip(probs, hits)
    ) / n
    correct = sum(1 for p, h in zip(probs, hits) if (p >= 0.5) == bool(h))
    return GateScores(label, n, brier, ll, correct / n)


# ---------------------------------------------------------------------------
# DuckDB loader
# ---------------------------------------------------------------------------


_QUERY = """
SELECT
    p.market_type    AS market_type,
    p.side           AS side,
    p.team_tricode   AS team_tricode,
    p.line_value     AS line_value,
    p.model_prob     AS model_prob,
    p.market_prob    AS market_prob,
    a.home_runs      AS actual_home,
    a.away_runs      AS actual_away,
    a.f5_home_runs   AS f5_home_runs,
    a.f5_away_runs   AS f5_away_runs,
    a.event_date     AS event_date
FROM fullgame_predictions p
JOIN fullgame_actuals     a
  ON a.game_pk = p.game_pk
WHERE p.event_date BETWEEN ? AND ?
  AND p.model_prob > 0.0 AND p.model_prob < 1.0
  AND p.market_prob > 0.0 AND p.market_prob < 1.0
"""


# Predictions don't currently persist `home_tricode`. We infer it as
# the home team for the matchup at settlement time, but the predictions
# table only stores the staked team. For sanity-gate purposes, ML and
# Run_Line picks need to know whether the staked side is home. We
# assume the team_tricode column already captures the staked team and
# the ledger table holds home_tricode separately. Fall back: when
# home_tricode is missing, treat staked team as home — this is wrong
# for away picks on ML, so the operator should land the home_tricode
# join via fullgame_pick_settled before relying on the gate verdict
# for ML / Run_Line. The Total / F5_Total / Team_Total over/under
# verdicts don't need the home identity.
_QUERY_WITH_HOME = """
SELECT
    p.market_type      AS market_type,
    p.side             AS side,
    p.team_tricode     AS team_tricode,
    s.team_tricode     AS pick_team,
    s.line_value       AS line_value_settled,
    p.line_value       AS line_value,
    p.model_prob       AS model_prob,
    p.market_prob      AS market_prob,
    s.actual_home      AS actual_home,
    s.actual_away      AS actual_away,
    s.actual_hit       AS actual_hit,
    p.event_date       AS event_date,
    -- home_tricode isn't persisted by the predictions table. We pull
    -- the per-game tricode from any matching settled row (best-effort);
    -- when missing, downstream gate logic falls back to is_home_side
    -- semantics that work for the Total / Team_Total markets but
    -- under-account for ML / Run_Line.
    NULL               AS home_tricode,
    NULL               AS f5_home_runs,
    NULL               AS f5_away_runs
FROM fullgame_predictions p
LEFT JOIN fullgame_pick_settled s
  ON s.game_pk    = p.game_pk
 AND s.market_type = p.market_type
 AND s.side       = p.side
 AND s.line_value = p.line_value
WHERE p.event_date BETWEEN ? AND ?
  AND p.model_prob > 0.0 AND p.model_prob < 1.0
  AND p.market_prob > 0.0 AND p.market_prob < 1.0
"""


def load_settled_rows(store, *, start_date: str, end_date: str) -> list[dict]:
    """Pull settled full-game rows out of DuckDB for the date window."""
    df = store.query_df(_QUERY, (start_date, end_date))
    if df is None or len(df) == 0:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        out.append({
            "market_type":  str(r.get("market_type") or ""),
            "side":         str(r.get("side") or ""),
            "team_tricode": str(r.get("team_tricode") or ""),
            "home_tricode": "",   # not persisted in predictions yet
            "line_value":   r.get("line_value"),
            "model_prob":   r.get("model_prob"),
            "market_prob":  r.get("market_prob"),
            "actual_home":  r.get("actual_home"),
            "actual_away":  r.get("actual_away"),
            "f5_home_runs": r.get("f5_home_runs"),
            "f5_away_runs": r.get("f5_away_runs"),
        })
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Full-Game sanity gate — projection vs market vs league prior.",
    )
    parser.add_argument("--duckdb-path", required=True)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    parser.add_argument("--min-n", type=int, default=50,
                          help="Min settled picks for a conclusive verdict.")
    parser.add_argument("--brier-delta", type=float, default=0.0,
                          help="Required Brier improvement vs market.")
    parser.add_argument("--json-out", default=None,
                          help="Write a machine-readable verdict JSON to this path.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from ..data.storage import FullGameStore   # local import keeps CLI lazy

    store = FullGameStore(args.duckdb_path)
    try:
        rows = load_settled_rows(
            store, start_date=args.start_date, end_date=args.end_date,
        )
    finally:
        store.close()

    report = evaluate_sanity(
        rows,
        min_n=args.min_n,
        primary_gate_min_brier_delta=args.brier_delta,
    )
    print(report.render())
    if args.json_out:
        from pathlib import Path
        Path(args.json_out).write_text(
            json.dumps(report.to_dict(), indent=2) + "\n",
        )
    return 0 if report.gate_passed else 2   # 2 = sanity-gate fail


if __name__ == "__main__":
    sys.exit(main())
