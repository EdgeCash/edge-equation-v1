"""
That K Report -- Monte Carlo strikeout simulator.

Consumes a (mean, dispersion) pair from model.project_strikeouts and
runs 5,000+ negative-binomial draws to produce a full distribution
over total Ks, along with over/under probabilities against the book's
line.

Determinism
-----------
Seed = sha256(game_id + pitcher_name + date).  Same slate row across
two runs produces identical stats -- critical for auditability (and
for the AI graphic renderer, which shows "5,000 EV Sims" on the
footer).

Negative binomial sampling
--------------------------
NB(r, p) with mean mu = r*(1-p)/p, dispersion r.  We parameterize by
(mu, r) and derive p = r / (r + mu).  Drawn by summing r independent
geometric trials (stdlib random.gauss isn't NB directly, but we use
the Poisson-Gamma mixture: X | lambda ~ Poisson(lambda),
lambda ~ Gamma(r, mu/r)).  That keeps the dependency surface to
random.gammavariate + the stdlib Poisson via sum-of-exponentials.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional


DEFAULT_N_SIMS = 5000


@dataclass(frozen=True)
class KProjection:
    """Final projection for one pitcher-start.  `mean` is the engine's
    point estimate; `prob_over` / `prob_under` are the empirical MC
    probabilities against the sportsbook's K line."""
    pitcher: str
    team: str
    opponent: str
    line: Decimal
    mean: Decimal
    stdev: Decimal
    p10: Decimal
    p50: Decimal
    p90: Decimal
    prob_over: Decimal
    prob_under: Decimal
    n_sims: int
    # How far above (positive) or below (negative) the line the engine's
    # point estimate lands, on the same K scale the book uses.
    edge_ks: Decimal
    # Edge scaled to probability space: prob_over - 0.5 when the model
    # leans over, or 0.5 - prob_over when it leans under.  Drives grade.
    edge_prob: Decimal
    lean: str                        # 'over' | 'under' | 'push'

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "team": self.team,
            "opponent": self.opponent,
            "line": str(self.line),
            "mean": str(self.mean),
            "stdev": str(self.stdev),
            "p10": str(self.p10),
            "p50": str(self.p50),
            "p90": str(self.p90),
            "prob_over": str(self.prob_over),
            "prob_under": str(self.prob_under),
            "n_sims": self.n_sims,
            "edge_ks": str(self.edge_ks),
            "edge_prob": str(self.edge_prob),
            "lean": self.lean,
        }


def _seed_int(*parts: object) -> int:
    m = hashlib.sha256()
    for p in parts:
        m.update(str(p).encode("utf-8"))
        m.update(b"|")
    return int.from_bytes(m.digest()[:4], "big")


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm for small lambda, Atkinson's for large.  Pure
    stdlib.  Upper lambda in pitcher-K context is ~15, so Knuth is
    perfectly fast and precise here."""
    if lam <= 0:
        return 0
    if lam < 30:
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= L:
                return k - 1
    # Fallback (rare in K context): normal approximation rounded.
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def _nb_sample(rng: random.Random, mean: float, r: float) -> int:
    """Negative binomial sample via Poisson-Gamma mixture.

    If X | lambda ~ Poisson(lambda) and lambda ~ Gamma(shape=r, scale=mean/r)
    then X ~ NegBinomial with E[X] = mean and Var[X] = mean + mean^2/r.
    """
    if mean <= 0:
        return 0
    lam = rng.gammavariate(r, mean / r)
    return _poisson(rng, lam)


def _percentile(sorted_vals: List[int], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = q * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = idx - lo
    return float(sorted_vals[lo]) + (
        float(sorted_vals[hi] - sorted_vals[lo]) * frac
    )


def _quantize(x: float, places: int = 2) -> Decimal:
    q = Decimal("1").scaleb(-places)
    return Decimal(str(x)).quantize(q)


def simulate_strikeouts(
    pitcher: str,
    team: str,
    opponent: str,
    line: float,
    mean: float,
    dispersion: float,
    seed_key: str = "",
    n_sims: int = DEFAULT_N_SIMS,
) -> KProjection:
    """Run n_sims NB draws, compute the empirical distribution, and
    return a KProjection carrying the stats the renderer needs.

    line: the sportsbook K line (e.g. 7.5).  Over/under probabilities
    are computed against this line -- samples above the line count as
    over, below as under; samples equal to the line (only possible on
    integer lines, rare) split 50/50 into a push.
    """
    rng = random.Random(_seed_int(
        "thatk", seed_key, pitcher, team, opponent, line, mean,
        dispersion, n_sims,
    ))
    samples: List[int] = []
    overs = 0
    unders = 0
    pushes = 0
    for _ in range(n_sims):
        k = _nb_sample(rng, mean, dispersion)
        samples.append(k)
        if k > line:
            overs += 1
        elif k < line:
            unders += 1
        else:
            pushes += 1
    samples.sort()

    n = len(samples)
    mean_emp = sum(samples) / n if n else 0.0
    if n > 1:
        var = sum((x - mean_emp) ** 2 for x in samples) / (n - 1)
        stdev = math.sqrt(var)
    else:
        stdev = 0.0

    p10 = _percentile(samples, 0.10)
    p50 = _percentile(samples, 0.50)
    p90 = _percentile(samples, 0.90)

    # Pushes split 50/50 between sides (standard book treatment).
    prob_over = (overs + 0.5 * pushes) / n if n else 0.0
    prob_under = (unders + 0.5 * pushes) / n if n else 0.0

    # Edge in K-scale (raw) and probability-scale (for grading).
    edge_ks = mean_emp - float(line)
    edge_prob_signed = prob_over - 0.5
    lean = "over" if prob_over > 0.5 else ("under" if prob_under > 0.5 else "push")
    edge_prob_mag = abs(edge_prob_signed)

    return KProjection(
        pitcher=pitcher,
        team=team,
        opponent=opponent,
        line=Decimal(str(line)),
        mean=_quantize(mean_emp, 2),
        stdev=_quantize(stdev, 3),
        p10=_quantize(p10, 1),
        p50=_quantize(p50, 1),
        p90=_quantize(p90, 1),
        prob_over=_quantize(prob_over, 3),
        prob_under=_quantize(prob_under, 3),
        n_sims=n,
        edge_ks=_quantize(edge_ks, 2),
        edge_prob=_quantize(edge_prob_mag, 3),
        lean=lean,
    )
