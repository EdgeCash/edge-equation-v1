"""
Home-field advantage (HFA) registry and calculator.

HFA is expressed in the units of each league's native scale:
- NBA / NFL: points
- NHL / MLB: goals / runs
- SOCCER:    log-gamma (added to the log of home lambda in a Poisson model)

Composition:
  total = (team_override if present else baseline) + venue_bonus

Team overrides replace the league baseline (they are absolute, not deltas),
reflecting venues with persistently unusual HFA (e.g. Denver altitude).
Venue bonuses stack on top of whichever baseline was selected (e.g. NFL dome).
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


HFA_BASELINE = {
    "NBA": Decimal('2.50'),
    "NFL": Decimal('1.80'),
    "NHL": Decimal('0.15'),
    "MLB": Decimal('0.08'),
    "SOCCER": Decimal('0.27'),
}

# Team overrides REPLACE the league baseline when a team plays at home.
HFA_TEAM_OVERRIDE = {
    ("NBA", "DEN"): Decimal('1.00'),
    ("NBA", "UTA"): Decimal('0.50'),
    ("NFL", "DEN"): Decimal('0.50'),
    ("MLB", "COL"): Decimal('0.40'),
}

# Venue bonuses ADD to whichever baseline was selected.
HFA_VENUE_BONUS = {
    ("NFL", "DOME"): Decimal('0.50'),
}


@dataclass(frozen=True)
class HFA:
    """Resolved home-field advantage for a single (sport, team, venue)."""
    sport: str
    team: Optional[str]
    baseline: Decimal
    team_override: Optional[Decimal]
    venue_bonus: Decimal
    total: Decimal

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "team": self.team,
            "baseline": str(self.baseline),
            "team_override": str(self.team_override) if self.team_override is not None else None,
            "venue_bonus": str(self.venue_bonus),
            "total": str(self.total),
        }


class HFACalculator:
    """
    Per-league home-field advantage:
    - get_home_adv(sport, team, context) -> HFA
    - baseline resolves from HFA_BASELINE
    - team override replaces baseline (absolute value, not delta)
    - venue bonus from context (e.g. {"venue": "DOME"}) stacks on top
    """

    @staticmethod
    def _baseline(sport: str) -> Decimal:
        if sport not in HFA_BASELINE:
            raise ValueError(
                f"Unknown sport '{sport}' for HFA. "
                f"Known: {sorted(HFA_BASELINE.keys())}"
            )
        return HFA_BASELINE[sport]

    @staticmethod
    def _team_override(sport: str, team: Optional[str]) -> Optional[Decimal]:
        if team is None:
            return None
        return HFA_TEAM_OVERRIDE.get((sport, team))

    @staticmethod
    def _venue_bonus(sport: str, context: Optional[dict]) -> Decimal:
        if not context:
            return Decimal('0.00')
        venue = context.get("venue")
        if venue is None:
            return Decimal('0.00')
        return HFA_VENUE_BONUS.get((sport, venue), Decimal('0.00'))

    @staticmethod
    def get_home_adv(
        sport: str,
        team: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> HFA:
        baseline = HFACalculator._baseline(sport)
        override = HFACalculator._team_override(sport, team)
        venue_bonus = HFACalculator._venue_bonus(sport, context)
        selected = override if override is not None else baseline
        total = (selected + venue_bonus).quantize(Decimal('0.000001'))
        return HFA(
            sport=sport,
            team=team,
            baseline=baseline,
            team_override=override,
            venue_bonus=venue_bonus,
            total=total,
        )
