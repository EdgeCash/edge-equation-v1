"""
Dixon-Coles low-score correlation parameter rho.

The Dixon-Coles (1997) correction adjusts the independent-Poisson joint
probability of (home_goals=x, away_goals=y) only for the four low-scoring
cells {(0,0), (0,1), (1,0), (1,1)}; all other cells are passed through
unchanged with tau = 1.

  tau(0,0) = 1 - lambda_h*lambda_a*rho
  tau(0,1) = 1 + lambda_h*rho
  tau(1,0) = 1 + lambda_a*rho
  tau(1,1) = 1 - rho
  otherwise tau = 1

Negative rho inflates low-score draws (0-0, 1-1), which matches top-flight
soccer data; tier-2 leagues see a smaller effect.

Per-tier init:
- SOCCER_TOP   rho = -0.13
- SOCCER_TIER2 rho = -0.08
- DEFAULT       rho =  0.00

Bounds: [-0.25, 0.15].
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
import math


RHO_INIT_REGISTRY = {
    "SOCCER_TOP": Decimal('-0.13'),
    "SOCCER_TIER2": Decimal('-0.08'),
    "DEFAULT": Decimal('0.00'),
}

RHO_LOWER_BOUND = Decimal('-0.25')
RHO_UPPER_BOUND = Decimal('0.15')


@dataclass(frozen=True)
class RhoParams:
    """Immutable Dixon-Coles rho configuration for a tier."""
    tier: str
    rho: Decimal

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "rho": str(self.rho),
        }


class DixonColesRho:
    """
    Dixon-Coles low-score correction:
    - for_tier: look up RhoParams for a registered tier
    - clamp_rho: bound rho to [-0.25, 0.15]
    - tau(x, y, lambda_home, lambda_away, rho): per-cell correction factor
    """

    @staticmethod
    def for_tier(tier: str) -> RhoParams:
        if tier not in RHO_INIT_REGISTRY:
            raise ValueError(
                f"Unknown tier '{tier}' for Dixon-Coles rho. "
                f"Known: {sorted(RHO_INIT_REGISTRY.keys())}"
            )
        return RhoParams(tier=tier, rho=RHO_INIT_REGISTRY[tier])

    @staticmethod
    def clamp_rho(rho: Decimal) -> Decimal:
        if rho < RHO_LOWER_BOUND:
            return RHO_LOWER_BOUND
        if rho > RHO_UPPER_BOUND:
            return RHO_UPPER_BOUND
        return rho

    @staticmethod
    def tau(
        x: int,
        y: int,
        lambda_home: float,
        lambda_away: float,
        rho: Decimal,
    ) -> Decimal:
        rho_d = Decimal(str(rho))
        lh = Decimal(str(lambda_home))
        la = Decimal(str(lambda_away))
        if x == 0 and y == 0:
            val = Decimal('1') - lh * la * rho_d
        elif x == 0 and y == 1:
            val = Decimal('1') + lh * rho_d
        elif x == 1 and y == 0:
            val = Decimal('1') + la * rho_d
        elif x == 1 and y == 1:
            val = Decimal('1') - rho_d
        else:
            val = Decimal('1')
        return val.quantize(Decimal('0.000001'))
