"""Tango-style empirical-Bayes shrinkage for NRFI feature inputs.

Pattern (matches what `kelly_adaptive.py` does for stake sizing, just
applied to performance rates instead of bankroll fractions):

    shrunk = (n / (n + n_prior)) * observed + (n_prior / (n + n_prior)) * prior

`n_prior` is the "regression to the mean" sample size — the number of
observations that prior carries weight equivalent to. Typical Tango
defaults for baseball:

    OBP             ~ 200 PA  (true-talent half-life)
    K%              ~ 70  PA
    BB%             ~ 120 PA
    Pitcher ERA     ~ 60  IP * 4 batters/IP = ~240 BF
    Pitcher xFIP    ~ 80  IP-equivalent
    HR/PA           ~ 320 PA  (very noisy, heavy regression)

These constants live here as a single source of truth so the rest of
the NRFI feature builder can pull them without re-deriving.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShrinkagePriors:
    """Tango n_prior table for the rates we actually use."""

    obp: float = 200.0
    k_pct: float = 70.0
    bb_pct: float = 120.0
    hr_pct: float = 320.0
    iso: float = 250.0
    woba: float = 180.0
    era: float = 240.0   # in batters-faced equivalent
    fip: float = 320.0
    whip: float = 240.0


DEFAULT_PRIORS = ShrinkagePriors()


# League averages (current 5-yr trailing). These are the means we
# regress toward when we have insufficient sample.
LEAGUE_MEAN_OBP = 0.318
LEAGUE_MEAN_K_PCT = 0.225
LEAGUE_MEAN_BB_PCT = 0.085
LEAGUE_MEAN_HR_PCT = 0.034
LEAGUE_MEAN_ISO = 0.150
LEAGUE_MEAN_WOBA = 0.314
LEAGUE_MEAN_ERA = 4.20
LEAGUE_MEAN_FIP = 4.10
LEAGUE_MEAN_WHIP = 1.30


def tango_shrink(observed: float, n_observed: float, prior_mean: float,
                  n_prior: float) -> float:
    """Empirical-Bayes shrinkage of a rate toward a prior mean.

    Parameters
    ----------
    observed : Player's observed rate (e.g., season OBP = .350).
    n_observed : Sample size behind that rate (e.g., 412 PA so far).
    prior_mean : League / cohort mean to regress toward (e.g., .318).
    n_prior : Tango "true-talent" sample size for this rate (e.g., 200).

    Returns
    -------
    The blended estimate. When `n_observed == 0` returns `prior_mean`;
    when `n_observed >> n_prior` returns ~`observed`.
    """
    n_observed = max(0.0, float(n_observed))
    n_prior = max(1e-6, float(n_prior))
    w = n_observed / (n_observed + n_prior)
    return w * float(observed) + (1.0 - w) * float(prior_mean)


def top_of_order_shrink(top3_obp: float, top3_pa: float = 0.0,
                         league_mean: float = LEAGUE_MEAN_OBP,
                         priors: ShrinkagePriors = DEFAULT_PRIORS) -> float:
    """Convenience: shrink a top-3 OBP using the standard OBP prior.

    `top3_pa` should be the *combined* PA for the three batters being
    aggregated. Pass 0 (or omit) when the lineup is projected and we
    don't yet have season counts; that yields the league mean.
    """
    return tango_shrink(top3_obp, top3_pa, league_mean, priors.obp)


def shrink_pitcher_era(era: float, batters_faced: float) -> float:
    return tango_shrink(era, batters_faced, LEAGUE_MEAN_ERA, DEFAULT_PRIORS.era)


def shrink_pitcher_fip(fip: float, batters_faced: float) -> float:
    return tango_shrink(fip, batters_faced, LEAGUE_MEAN_FIP, DEFAULT_PRIORS.fip)


def shrink_pitcher_k_pct(k_pct: float, batters_faced: float) -> float:
    return tango_shrink(k_pct, batters_faced, LEAGUE_MEAN_K_PCT, DEFAULT_PRIORS.k_pct)


def shrink_pitcher_bb_pct(bb_pct: float, batters_faced: float) -> float:
    return tango_shrink(bb_pct, batters_faced, LEAGUE_MEAN_BB_PCT, DEFAULT_PRIORS.bb_pct)
