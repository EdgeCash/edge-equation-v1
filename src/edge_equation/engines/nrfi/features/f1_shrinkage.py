"""Empirical-Bayes shrinkage tuned for first-inning sample sizes.

The existing ``integration/shrinkage.py`` shrinks pitcher season stats
toward the league mean using batters-faced as the sample-size signal.
Half-shrink point is ~1000 BF (a full-season starter) which is right
for season-level rates.

First-inning splits live on a different scale: a starter who's made
30 starts has only ~30 first innings = ~120 PAs. Naive use of the
season-level shrinker treats those as essentially zero-sample, which
collapses every F1 stat back to the league prior and erases the
signal we're trying to surface.

This module supplies a separate set of EB shrinkers calibrated on
F1-specific PA volumes:

  * Half-shrink BB% at ~70 F1 PAs (per FanGraphs reliability tables
    for walk rate stabilization scaled to inning-1 events).
  * Half-shrink K% at ~150 F1 PAs.
  * Half-shrink runs/inning at ~25 F1 innings.
  * Half-shrink umpire CSA in F1 at ~150 F1 calls (ump sample
    accumulates faster than pitcher because every pitch counts).

Same shape as ``integration/shrinkage.py``: ``shrink(value, sample) ->
shrunk_value`` returning a float in the natural units of each stat.
"""

from __future__ import annotations


# League priors for first-inning rates. These match the empty-split
# defaults in ``splits.py::_empty_pitcher_split`` so tests stay
# consistent across the two modules.
_F1_LEAGUE_PRIORS: dict[str, float] = {
    "k_pct": 0.220,
    "bb_pct": 0.085,
    "hr_pct": 0.034,
    "runs_per_inn": 0.55,         # league avg first-inning runs per side
    "ump_csa": 0.0,               # called strikes above avg, F1-specific
    "ump_walk_rate": 0.085,       # walks issued per F1 PA, league avg
}


# Half-shrink points (in stat-specific sample units) where the shrunk
# value sits halfway between the prior and the observed value.
# Smaller = signal stabilizes faster; larger = more conservative
# regression to the mean.
_F1_HALF_SHRINK: dict[str, float] = {
    "k_pct": 150.0,            # PAs --- K% stabilizes slower than BB%
    "bb_pct": 70.0,            # PAs --- walk rate stabilizes fastest
    "hr_pct": 250.0,           # PAs --- HR rate is high-variance
    "runs_per_inn": 25.0,      # innings (not PAs); volatile stat
    "ump_csa": 150.0,          # F1 called pitches per ump
    "ump_walk_rate": 80.0,     # F1 PAs per ump
}


def _eb_shrink(observed: float, prior: float, sample: float, half: float) -> float:
    """Standard empirical-Bayes posterior mean.

    Posterior weight on the observation is ``sample / (sample +
    half)`` --- when ``sample == half`` the result is exactly the
    midpoint of prior and observed. ``sample == 0`` returns the
    prior; ``sample -> infinity`` returns the observation.
    """
    if sample <= 0:
        return prior
    if half <= 0:
        return observed
    w = sample / (sample + half)
    return w * observed + (1.0 - w) * prior


def shrink_f1_k_pct(observed: float, sample_pa: float) -> float:
    """Shrink F1-specific pitcher K% toward the league F1 prior."""
    return _eb_shrink(
        observed, _F1_LEAGUE_PRIORS["k_pct"], sample_pa,
        _F1_HALF_SHRINK["k_pct"],
    )


def shrink_f1_bb_pct(observed: float, sample_pa: float) -> float:
    """Shrink F1-specific pitcher BB% toward the league F1 prior."""
    return _eb_shrink(
        observed, _F1_LEAGUE_PRIORS["bb_pct"], sample_pa,
        _F1_HALF_SHRINK["bb_pct"],
    )


def shrink_f1_hr_pct(observed: float, sample_pa: float) -> float:
    """Shrink F1-specific pitcher HR% toward the league F1 prior."""
    return _eb_shrink(
        observed, _F1_LEAGUE_PRIORS["hr_pct"], sample_pa,
        _F1_HALF_SHRINK["hr_pct"],
    )


def shrink_f1_runs_per_inn(observed: float, sample_innings: float) -> float:
    """Shrink F1 runs/inning rate toward the league prior.

    The sample dimension here is *innings pitched* not PAs because
    runs are an inning-level statistic.
    """
    return _eb_shrink(
        observed, _F1_LEAGUE_PRIORS["runs_per_inn"], sample_innings,
        _F1_HALF_SHRINK["runs_per_inn"],
    )


def shrink_ump_f1_csa(observed: float, sample_calls: float) -> float:
    """Shrink umpire F1-specific called-strike-above-avg toward zero.

    Umpire CSA is naturally centered on 0 (it's a delta vs the
    rulebook strike zone), so the prior of 0.0 is the correct
    anchor for "we have no idea." Half-shrinks at 150 F1 called
    pitches.
    """
    return _eb_shrink(
        observed, _F1_LEAGUE_PRIORS["ump_csa"], sample_calls,
        _F1_HALF_SHRINK["ump_csa"],
    )


def shrink_ump_f1_walk_rate(observed: float, sample_pa: float) -> float:
    """Shrink umpire F1-specific BB-per-PA toward the league prior."""
    return _eb_shrink(
        observed, _F1_LEAGUE_PRIORS["ump_walk_rate"], sample_pa,
        _F1_HALF_SHRINK["ump_walk_rate"],
    )


def league_priors() -> dict[str, float]:
    """Frozen view of the priors used by the shrinkage helpers.

    Exposed so ``feature_engineering.py`` can default missing inputs
    to the same numbers without re-typing them.
    """
    return dict(_F1_LEAGUE_PRIORS)
