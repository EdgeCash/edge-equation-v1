"""Props sanity gate — does the projection beat the market?

The hard quality bar before any props pick goes to the public ledger.
The Poisson projection's job is to find lines where the model's
probability differs from the vig-corrected market probability by
enough to justify the risk. If the projection *can't* outperform the
market on a holdout slice, we have no edge and we shouldn't be
publishing picks.

Two baselines, in order of strictness:

1. **Market (primary).** The vig-corrected book probability is the
   sharpest single number we have. If our projection's Brier on
   settled lines isn't strictly better than the market's Brier on
   the same lines, we're trading randomly — and the public ledger
   would just publish noise. **Gate fails** if model Brier ≥ market
   Brier on any meaningful sample.

2. **League prior (secondary).** A trivial baseline — replace the
   per-player rate with the league prior, run the same Poisson
   formula. This catches "did we even improve over assuming every
   player is league-average?" Even if we beat the market, we should
   beat this by a wide margin or the model is doing something weird.

Inputs come from the existing ``prop_predictions`` × ``prop_actuals``
join in DuckDB. No extra data plumbing.

CLI
~~~

::

    python -m edge_equation.engines.props_prizepicks.evaluation.sanity \\
        --duckdb-path data/props_cache/props.duckdb \\
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


# ---------------------------------------------------------------------------
# League priors — coarse fallbacks for each market when own-rate is
# missing. Values are per-PA (batter) or per-BF (pitcher) rates.
# Mirrored from the projection module's defaults so the sanity gate
# uses the same trivial baseline the live projection falls back to.
# ---------------------------------------------------------------------------


_LEAGUE_PRIORS: dict[str, float] = {
    # Canonical market names match `markets.PropMarket.canonical` and what
    # the daily orchestrator persists in `prop_predictions.market_type`.
    "HR":          0.030,   # per-PA
    "Hits":        0.245,   # per-PA
    "Total_Bases": 0.395,   # per-PA
    "RBI":         0.115,   # per-PA
    "K":           0.230,   # per-BF
}


# Coarse expected-volume defaults; align with ProjectionKnobs.
_EXPECTED_VOLUME: dict[str, float] = {
    "HR":          4.1,
    "Hits":        4.1,
    "Total_Bases": 4.1,
    "RBI":         4.1,
    "K":           22.0,
}


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
            "Props sanity gate",
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
    min_n: int = 50,
    primary_gate_min_brier_delta: float = 0.0,
) -> SanityReport:
    """Score the model / market / league-prior probabilities against
    settled actuals; return a SanityReport with pass/fail verdicts.

    Each row in ``rows`` is expected to carry::

        market_type    str       — used to pick the league prior
        line_value     float     — the prop line
        side           str       — 'over' / 'under' / 'yes' / 'no'
        model_prob     float     — projection's prob the side wins
        market_prob    float     — vig-corrected market prob (devigged)
        actual_value   float     — what actually happened

    Rows with missing fields are silently skipped — defensive for
    in-progress backfills and partial settle passes.

    Parameters
    ----------
    min_n : Minimum settled-pick count required for the gate to be
        considered conclusive. Below this, the gate auto-fails with a
        note (insufficient evidence to publish).
    primary_gate_min_brier_delta : How much MORE Brier-skill the model
        must have over the market. Default 0.0 means strictly better.
        Set to 0.005 (or similar) once you want a conservative buffer.
    """
    report = SanityReport()

    pairs: list[tuple[float, float, float, int]] = []
    # (model_prob, market_prob, league_prior_prob, hit_int)
    for r in rows:
        try:
            actual = float(r["actual_value"])
            line = float(r["line_value"])
            side = str(r["side"]).strip().lower()
            mp = float(r["model_prob"])
            mkt = float(r["market_prob"])
        except (KeyError, TypeError, ValueError):
            continue
        if mp <= 0.0 or mp >= 1.0 or mkt <= 0.0 or mkt >= 1.0:
            continue

        hit = _did_side_hit(actual, line, side)
        if hit is None:
            continue
        league_p = _league_prior_prob(
            market_type=str(r.get("market_type", "")),
            line_value=line,
            side=side,
        )
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

    # Gate verdicts. Both require min_n picks to clear.
    if report.n_picks >= min_n:
        # Primary: Brier(model) < Brier(market) − threshold
        report.primary_gate_passed = (
            report.market.brier - report.model.brier
            > primary_gate_min_brier_delta
        )
        # Secondary: Brier(model) strictly < Brier(league_prior).
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
# Scoring helpers
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


def _did_side_hit(actual: float, line: float, side: str) -> Optional[int]:
    """Return 1 if the bet side won, 0 if it lost, None for a push."""
    side = side.strip().lower()
    if side in ("over", "yes"):
        if math.isclose(actual, line):
            return None
        return 1 if actual > line else 0
    if side in ("under", "no"):
        if math.isclose(actual, line):
            return None
        return 1 if actual < line else 0
    return None


def _league_prior_prob(*, market_type: str, line_value: float, side: str) -> float:
    """League-prior probability for the chosen side.

    Treats the per-PA / per-BF rate as a Poisson rate, multiplies by
    expected volume, and computes P(X > line) the same way the
    projection module does. Falls back to 0.5 when the market type is
    unknown — that gives the gate a no-information baseline rather
    than an artificially-biased one.
    """
    rate = _LEAGUE_PRIORS.get(market_type)
    volume = _EXPECTED_VOLUME.get(market_type)
    if rate is None or volume is None:
        return 0.5
    lam = rate * volume
    p_over = _poisson_p_over(line_value, lam)
    s = side.strip().lower()
    if s in ("over", "yes"):
        return p_over
    if s in ("under", "no"):
        return 1.0 - p_over
    return 0.5


def _poisson_p_over(line: float, lam: float) -> float:
    """P(X > line) for X ~ Poisson(lam). Closed-form sum, fine for k≤30."""
    k = int(math.floor(line))
    if k < 0:
        return 1.0
    lam = max(0.0, float(lam))
    if lam == 0.0:
        return 0.0
    total = 0.0
    p = math.exp(-lam)
    total += p
    for i in range(1, k + 1):
        p *= lam / i
        total += p
    return max(0.0, 1.0 - min(1.0, total))


# ---------------------------------------------------------------------------
# DuckDB loader
# ---------------------------------------------------------------------------


_QUERY = """
SELECT
    p.market_type    AS market_type,
    p.line_value     AS line_value,
    p.side           AS side,
    p.model_prob     AS model_prob,
    p.market_prob    AS market_prob,
    a.actual_value   AS actual_value
FROM prop_predictions p
JOIN prop_actuals     a
  ON a.game_pk     = p.game_pk
 AND a.market_type = p.market_type
 AND a.player_name = p.player_name
WHERE p.event_date BETWEEN ? AND ?
  AND p.model_prob > 0.0 AND p.model_prob < 1.0
  AND p.market_prob > 0.0 AND p.market_prob < 1.0
"""


def load_settled_rows(store, *, start_date: str, end_date: str) -> list[dict]:
    """Pull settled props rows out of DuckDB for the date window."""
    df = store.query_df(_QUERY, (start_date, end_date))
    if df is None or len(df) == 0:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        out.append({
            "market_type":  str(r.get("market_type") or ""),
            "line_value":   r.get("line_value"),
            "side":         str(r.get("side") or ""),
            "model_prob":   r.get("model_prob"),
            "market_prob":  r.get("market_prob"),
            "actual_value": r.get("actual_value"),
        })
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Props sanity gate — projection vs market vs league prior.",
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

    from ..data.storage import PropsStore   # local import keeps CLI lazy

    store = PropsStore(args.duckdb_path)
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
