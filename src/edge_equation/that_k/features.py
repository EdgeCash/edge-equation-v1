"""
That K Report -- feature importance tracker.

Turns each pitcher's KProjectionInputs into an "importance" vector:
how much each factor (SwStr, CSW, arsenal, handedness, umpire,
weather, form) shifted the total multiplier away from 1.0 on a log
scale.  Aggregate over a slate gives a daily feature-importance
summary the main Edge Equation engine can mine for weight tuning.

Why log-scale:
    The projection stacks factors MULTIPLICATIVELY; the easiest way
    to attribute total movement is the log decomposition:

        log(total_adj) = sum_i log(factor_i)

    A factor's share of the movement is |log(factor_i)| / sum(|log|).
    Sign is preserved separately so we can also report which
    direction each factor pulled on any given start.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List

from edge_equation.that_k.model import KProjectionInputs


# Ordered tuple of (attribute_name, display_label).  Order stable so
# downstream JSON consumers and renderers see a predictable field
# layout.
_FACTORS = (
    ("swstr_adj", "swstr"),
    ("csw_adj", "csw"),
    ("arsenal_adj", "arsenal"),
    ("handedness_adj", "handedness"),
    ("umpire_adj", "umpire"),
    ("weather_adj", "weather"),
    ("form_adj", "form"),
)


@dataclass(frozen=True)
class FeatureContribution:
    """One factor's contribution for one pitcher-start."""
    factor: str
    multiplier: float        # raw adjustment (1.0 = neutral)
    log_abs: float           # |log(multiplier)|
    direction: int           # +1 / -1 / 0

    def to_dict(self) -> dict:
        return {
            "factor": self.factor,
            "multiplier": self.multiplier,
            "log_abs": self.log_abs,
            "direction": self.direction,
        }


def contributions_for_inputs(
    inputs: KProjectionInputs,
) -> List[FeatureContribution]:
    """Break one KProjectionInputs into a list of FeatureContribution
    rows.  Zero-movement factors (multiplier == 1.0) still appear so
    the aggregator sees the full feature space."""
    out: List[FeatureContribution] = []
    for attr, label in _FACTORS:
        mult = float(getattr(inputs, attr))
        if mult <= 0:
            # Defensive: log() is undefined; treat as neutral.
            out.append(FeatureContribution(
                factor=label, multiplier=mult,
                log_abs=0.0, direction=0,
            ))
            continue
        lg = math.log(mult)
        out.append(FeatureContribution(
            factor=label,
            multiplier=mult,
            log_abs=abs(lg),
            direction=1 if lg > 0 else (-1 if lg < 0 else 0),
        ))
    return out


@dataclass(frozen=True)
class FeatureImportanceRow:
    """Per-pitcher importance row for the metrics artifact."""
    pitcher: str
    contributions: List[FeatureContribution]
    total_log_abs: float     # sum of |log| across all factors

    def shares(self) -> Dict[str, float]:
        """Normalize into a distribution over factors.  Sums to 1.0
        for rows with any movement; {} for a neutral start."""
        if self.total_log_abs <= 0:
            return {c.factor: 0.0 for c in self.contributions}
        return {
            c.factor: c.log_abs / self.total_log_abs
            for c in self.contributions
        }

    def to_dict(self) -> dict:
        return {
            "pitcher": self.pitcher,
            "contributions": [c.to_dict() for c in self.contributions],
            "total_log_abs": self.total_log_abs,
            "shares": self.shares(),
        }


def importance_for_row(
    pitcher: str,
    inputs: KProjectionInputs,
) -> FeatureImportanceRow:
    """Build a FeatureImportanceRow from a single KProjectionInputs."""
    cs = contributions_for_inputs(inputs)
    total = sum(c.log_abs for c in cs)
    return FeatureImportanceRow(
        pitcher=pitcher, contributions=cs, total_log_abs=total,
    )


def aggregate_importance(rows: Iterable[FeatureImportanceRow]) -> dict:
    """Slate-level aggregate: mean share per factor + count of starts
    where that factor was the single biggest driver.  Fed into the
    metrics JSON so the main engine can mine it later."""
    rows = list(rows)
    by_factor_share: Dict[str, float] = {label: 0.0 for _, label in _FACTORS}
    by_factor_lead: Dict[str, int] = {label: 0 for _, label in _FACTORS}
    n = 0
    for r in rows:
        if r.total_log_abs <= 0:
            continue
        n += 1
        shares = r.shares()
        for label, share in shares.items():
            by_factor_share[label] += share
        # Single biggest driver for this pitcher.
        top = max(r.contributions, key=lambda c: c.log_abs)
        if top.log_abs > 0:
            by_factor_lead[top.factor] += 1
    mean_share = {
        label: (by_factor_share[label] / n) if n else 0.0
        for label in by_factor_share
    }
    return {
        "n_rows": n,
        "mean_share": mean_share,
        "lead_count": by_factor_lead,
    }
