"""
Phase 29 -- LEAGUE_TO_SPORT["NBA"] = "NBA" fix.

Pre-fix the mapping pointed NBA at "NCAA_Basketball" (because NBA
wasn't in SPORT_CONFIG yet). The downstream consequence was a
double whammy:

  1. NBA games inherited NCAA's home_adv (0.170) + Pythagorean
     exponent + decay lambda -- all wrong for the NBA pace / parity
     regime.
  2. FeatureComposer.compose pulls historical results filtered by
     the engine sport. With NBA mapped to "NCAA_Basketball" it
     looked up COLLEGE basketball history to compute strength
     ratings for NBA teams -- producing nonsense ratios like
     4.22 vs 0.24 on a Knicks @ Hawks matchup that pushed
     fair_prob to the 0.99 clamp and triggered the both-sides-A+
     bug pattern.

Phase 27b added a real NBA SPORT_CONFIG entry. This phase points
LEAGUE_TO_SPORT at it.
"""
import pytest

from edge_equation.config.sport_config import SPORT_CONFIG
from edge_equation.ingestion.schema import LEAGUE_TO_SPORT


def test_nba_maps_to_its_own_sport_not_ncaa_basketball():
    """The whole point of Phase 29: prevent the cross-league strength
    contamination that produced the +43% Hawks/Knicks pattern."""
    assert LEAGUE_TO_SPORT["NBA"] == "NBA"
    assert LEAGUE_TO_SPORT["NBA"] != "NCAA_Basketball"


def test_every_mapped_sport_exists_in_sport_config():
    """Defensive: any LEAGUE_TO_SPORT value must resolve in SPORT_CONFIG
    so the engine can grade it. A typo or missing entry is the same
    class of bug that caused Phase 27b's NBA crash."""
    for league, sport in LEAGUE_TO_SPORT.items():
        assert sport in SPORT_CONFIG, (
            f"League {league!r} maps to sport {sport!r} which is not "
            f"present in SPORT_CONFIG -- the engine will raise on the "
            f"first slate that includes it."
        )


def test_sport_config_for_nba_is_basketball_not_baseball():
    """Sanity check on the actual NBA values -- Pythagorean exponent
    around 14 (basketball), home_adv around 0.15, NOT the MLB-style
    1.83 exponent that would land us on the wrong scoring model."""
    from decimal import Decimal
    cfg = SPORT_CONFIG["NBA"]
    assert cfg["pythagorean_exponent"] >= Decimal("10")
    assert Decimal("0.08") <= cfg["home_adv"] <= Decimal("0.20")


def test_ncaab_still_maps_to_ncaa_basketball():
    """NCAAB stays where it was -- this fix is targeted at NBA only."""
    assert LEAGUE_TO_SPORT["NCAAB"] == "NCAA_Basketball"


def test_leagues_unique_per_sport_config_lookup():
    """Two leagues sharing the same sport (NBA + NCAAB) is fine -- but
    NBA must NOT alias to NCAA's sport key. This test guards against
    a future revert that re-aliases them together."""
    assert LEAGUE_TO_SPORT["NBA"] != LEAGUE_TO_SPORT["NCAAB"]
