"""Per-PA Monte Carlo simulation of a half-inning.

Outcomes are drawn from a flattened multinomial constructed from the
pitcher's first-inning K%/BB%/HBP/H/HR rates. We do not model situational
base-state value precisely (this is a half-inning sim, not a full
PA-by-PA game) — instead we collapse to a coarse "runs allowed in this
half-inning" distribution that empirically matches league NRFI rates
when seeded with league-average inputs.

Returned bounds are equal-tailed credibility intervals on the joint
P(NRFI) across both halves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..config import MonteCarloConfig


@dataclass
class MCResult:
    p_nrfi: float
    low: float
    high: float
    mean_runs: float
    std_runs: float


def _half_outcome_rates(features: Mapping[str, float], side: str) -> dict[str, float]:
    """Pull per-PA outcome probabilities for one half-inning."""
    p_k = float(features.get(f"{side}_first_inn_k_pct",
                              features.get(f"{side}_k_pct", 0.225)))
    p_bb = float(features.get(f"{side}_first_inn_bb_pct",
                               features.get(f"{side}_bb_pct", 0.085)))
    p_hbp = 0.011
    p_hr = float(features.get(f"{side}_first_inn_hr_pct",
                               features.get(f"{side}_hr_pct", 0.034)))
    # Approximate non-HR hit rate from BABIP-ish remainder
    p_h = max(0.0, 0.235 - p_hr)
    # Outs that aren't strikeouts (BIP outs)
    p_other_out = max(0.0, 1.0 - (p_k + p_bb + p_hbp + p_hr + p_h))
    return {"k": p_k, "bb": p_bb, "hbp": p_hbp, "hr": p_hr,
            "h": p_h, "out": p_other_out}


def _simulate_half(rates: Mapping[str, float], rng: np.random.Generator,
                   max_pa: int) -> int:
    """Return runs scored in one half-inning."""
    outs = 0
    on1 = on2 = on3 = 0
    runs = 0
    pa = 0
    keys = ("k", "bb", "hbp", "hr", "h", "out")
    p = np.array([rates[k] for k in keys])
    p = p / p.sum()
    while outs < 3 and pa < max_pa:
        ev = keys[int(rng.choice(len(keys), p=p))]
        pa += 1
        if ev == "k":
            outs += 1
        elif ev == "bb" or ev == "hbp":
            # Force runners up only when forced.
            if on1 and on2 and on3:
                runs += 1
            elif on1 and on2:
                on3 = 1
            elif on1:
                on2 = 1
            on1 = 1
        elif ev == "h":
            # Treat as single: runner from 3rd scores, others advance.
            runs += on3
            on3 = on2
            on2 = on1
            on1 = 1
        elif ev == "hr":
            runs += 1 + on1 + on2 + on3
            on1 = on2 = on3 = 0
        else:  # out
            outs += 1
            # Advance runner from 3rd ~30% on outs (productive outs)
            if on3 and outs < 3 and rng.random() < 0.30:
                runs += 1
                on3 = 0
    return runs


def simulate_first_inning(features: Mapping[str, float],
                           cfg: MonteCarloConfig) -> MCResult:
    """Simulate the entire first inning and return P(NRFI) + CI."""
    rng = np.random.default_rng(cfg.rng_seed)
    home_rates = _half_outcome_rates(features, "home_p")
    away_rates = _half_outcome_rates(features, "away_p")

    runs_total = np.empty(cfg.n_simulations, dtype=np.int32)
    nrfi_flags = np.empty(cfg.n_simulations, dtype=bool)
    for i in range(cfg.n_simulations):
        # Top of 1: AWAY hits (vs HOME pitcher).
        top = _simulate_half(home_rates, rng, cfg.max_pa_per_half)
        # Bottom of 1: HOME hits (vs AWAY pitcher).
        bot = _simulate_half(away_rates, rng, cfg.max_pa_per_half)
        total = top + bot
        runs_total[i] = total
        nrfi_flags[i] = total == 0

    p_nrfi = float(nrfi_flags.mean())
    # Bootstrap CI via beta-binomial Wilson-style closed form.
    n = cfg.n_simulations
    se = np.sqrt(p_nrfi * (1 - p_nrfi) / n)
    z = 1.6449 if cfg.confidence_alpha == 0.10 else 1.96
    return MCResult(
        p_nrfi=p_nrfi,
        low=max(0.0, p_nrfi - z * se),
        high=min(1.0, p_nrfi + z * se),
        mean_runs=float(runs_total.mean()),
        std_runs=float(runs_total.std()),
    )
