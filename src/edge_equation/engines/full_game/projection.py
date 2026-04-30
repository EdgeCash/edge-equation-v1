"""Per-team Poisson projection for MLB full-game markets.

The full-game engine projects four market shapes off the same per-
team rates input:

* **Game Total (Over/Under)** — λ_total = home_expected_runs +
  away_expected_runs; P(over line) = 1 − Poisson_CDF(line, λ_total).
* **F5 Total** — same shape, with λ scaled by `f5_share_of_total`.
* **Run Line / Spread** — Skellam (difference of Poissons) for the
  margin distribution; P(home wins by ≥1.5) integrates the upper
  tail.
* **Moneyline / F5_ML** — Skellam P(margin > 0).
* **Team Total** — Poisson(λ_team_expected_runs).

Expected-runs construction (for one team facing the other):

    λ_team = team_offensive_strength × opp_pitching_strength × LEAGUE_RPG

Both strengths are Bayesian-blended toward 1.0 (league average) with
`prior_weight_games` pseudo-counts so early-season teams don't
project as dominant or hopeless on a 5-game sample.

Home-field advantage applies a `home_field_advantage_pct`
multiplicative bump to the home team's expected runs and to the
home-side win probability via a small Skellam location shift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .config import ProjectionKnobs
from .data.team_rates import (
    LEAGUE_RUNS_ALLOWED_PER_GAME,
    LEAGUE_RUNS_PER_GAME,
    TeamRollingRates,
    bayesian_blend,
)
from .markets import FullGameMarket
from .odds_fetcher import FullGameLine


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectedFullGameSide:
    """One side of one full-game market with a model probability."""
    market: FullGameMarket
    side: str
    line_value: Optional[float]
    model_prob: float
    confidence: float
    # Audit trail — which λs the projection used.
    lam_home: float = 0.0
    lam_away: float = 0.0
    lam_used: float = 0.0
    blend_n_home: int = 0
    blend_n_away: int = 0


# ---------------------------------------------------------------------------
# Poisson math (closed-form)
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


# ---------------------------------------------------------------------------
# Skellam (difference of two independent Poissons)
# ---------------------------------------------------------------------------


def _skellam_p_diff_gt(threshold: float, lam_a: float, lam_b: float,
                          *, max_runs: int = 25) -> float:
    """P(X - Y > threshold) where X~Poisson(λ_a), Y~Poisson(λ_b).

    Threshold of -1.5 is "spread of -1.5 (favourite covers when wins
    by 2+)". 0.5 is "wins by 1+" (moneyline).

    `max_runs` truncates each Poisson tail at 25 — well past every
    realistic MLB game (the all-time scoring record is ~30 total
    runs). At λ=4.5 the Poisson PMF at 25 is ~10⁻¹⁵, negligible.
    """
    total = 0.0
    # P(diff > threshold) = sum over all (a, b) where a - b > threshold.
    for a in range(0, max_runs + 1):
        pa = _poisson_pmf(a, lam_a)
        if pa <= 0:
            continue
        # b < a - threshold ⇒ b ≤ floor(a - threshold - 1e-9)
        # We want a - b > threshold ⇒ b < a - threshold.
        b_upper = a - threshold - 1e-9
        if b_upper < 0:
            continue
        b_max = int(math.floor(b_upper))
        # Sum P(Y ≤ b_max) = Poisson_CDF(b_max, λ_b)
        cdf = _poisson_cdf(b_max, lam_b)
        total += pa * cdf
    return min(1.0, max(0.0, total))


# ---------------------------------------------------------------------------
# Projection inputs
# ---------------------------------------------------------------------------


def _resolve_lambda(
    home_rates: Optional[TeamRollingRates],
    away_rates: Optional[TeamRollingRates],
    knobs: ProjectionKnobs,
) -> tuple[float, float]:
    """Build (λ_home, λ_away) for the matchup using Bayesian-blended
    offensive × pitching strengths.

    λ_home = blended(home_off_strength) × blended(away_pitch_strength)
             × LEAGUE_RPG × HFA_bump
    λ_away = blended(away_off_strength) × blended(home_pitch_strength)
             × LEAGUE_RPG
    """
    pw = knobs.prior_weight_games
    league_rpg = LEAGUE_RUNS_PER_GAME

    if home_rates is None:
        home_off_blended = league_rpg
        home_pitch_blended = league_rpg
    else:
        home_off_blended = bayesian_blend(
            home_rates.runs_per_game, home_rates.n_games,
            league_rpg, pw,
        )
        home_pitch_blended = bayesian_blend(
            home_rates.runs_allowed_per_game, home_rates.n_games,
            LEAGUE_RUNS_ALLOWED_PER_GAME, pw,
        )

    if away_rates is None:
        away_off_blended = league_rpg
        away_pitch_blended = league_rpg
    else:
        away_off_blended = bayesian_blend(
            away_rates.runs_per_game, away_rates.n_games,
            league_rpg, pw,
        )
        away_pitch_blended = bayesian_blend(
            away_rates.runs_allowed_per_game, away_rates.n_games,
            LEAGUE_RUNS_ALLOWED_PER_GAME, pw,
        )

    # Strengths are per-team RPG; product / league = expected matchup
    # multiplier on league RPG.
    home_off_strength = home_off_blended / league_rpg
    away_pitch_strength = away_pitch_blended / LEAGUE_RUNS_ALLOWED_PER_GAME
    away_off_strength = away_off_blended / league_rpg
    home_pitch_strength = home_pitch_blended / LEAGUE_RUNS_ALLOWED_PER_GAME

    lam_home = home_off_strength * away_pitch_strength * league_rpg
    lam_away = away_off_strength * home_pitch_strength * league_rpg
    # Apply HFA only to the home offensive lift.
    lam_home *= (1.0 + knobs.home_field_advantage_pct)
    return float(lam_home), float(lam_away)


def _confidence_for_blend(min_n: int, prior_weight: float) -> float:
    """Map sample size to confidence in [0.30, 0.85] — same scale as Props."""
    if min_n <= 0:
        return 0.30
    own_weight = min_n / (min_n + max(1.0, prior_weight))
    return 0.30 + 0.55 * float(own_weight)


# ---------------------------------------------------------------------------
# Public projection API
# ---------------------------------------------------------------------------


def project_full_game_market(
    line: FullGameLine, *,
    home_rates: Optional[TeamRollingRates] = None,
    away_rates: Optional[TeamRollingRates] = None,
    knobs: Optional[ProjectionKnobs] = None,
) -> ProjectedFullGameSide:
    """Project the staked side of `line` to a model probability.

    Uses per-team Bayesian-blended runs-scored / runs-allowed rates
    when supplied; falls back to the league prior when they're not.
    """
    knobs = knobs or ProjectionKnobs()
    lam_home, lam_away = _resolve_lambda(home_rates, away_rates, knobs)
    lam_total = lam_home + lam_away

    side_lower = (line.side or "").strip().lower()
    market = line.market.canonical

    if market == "Total":
        # Over/Under on game total.
        line_value = line.line_value if line.line_value is not None else 8.5
        p_over = _prob_over_poisson(line_value, lam_total)
        prob = p_over if side_lower == "over" else (1.0 - p_over)
        lam_used = lam_total
    elif market == "F5_Total":
        f5_total = lam_total * knobs.f5_share_of_total
        line_value = line.line_value if line.line_value is not None else 4.5
        p_over = _prob_over_poisson(line_value, f5_total)
        prob = p_over if side_lower == "over" else (1.0 - p_over)
        lam_used = f5_total
    elif market == "Team_Total":
        # description column on the outcome carried the team — `team_tricode`
        # set during normalisation. Use that team's λ.
        line_value = line.line_value if line.line_value is not None else 4.5
        is_home = bool(line.team_tricode and
                          line.team_tricode == line.home_tricode)
        team_lam = lam_home if is_home else lam_away
        p_over = _prob_over_poisson(line_value, team_lam)
        prob = p_over if side_lower == "over" else (1.0 - p_over)
        lam_used = team_lam
    elif market == "ML":
        # Moneyline — P(home margin > 0) for home pick, P(away margin > 0) flip.
        is_home = bool(line.team_tricode and
                          line.team_tricode == line.home_tricode)
        if is_home:
            prob = _skellam_p_diff_gt(0.0, lam_home, lam_away)
        else:
            prob = _skellam_p_diff_gt(0.0, lam_away, lam_home)
        lam_used = lam_total
    elif market == "F5_ML":
        f5_home = lam_home * knobs.f5_share_of_total
        f5_away = lam_away * knobs.f5_share_of_total
        is_home = bool(line.team_tricode and
                          line.team_tricode == line.home_tricode)
        if is_home:
            prob = _skellam_p_diff_gt(0.0, f5_home, f5_away)
        else:
            prob = _skellam_p_diff_gt(0.0, f5_away, f5_home)
        lam_used = f5_home + f5_away
    elif market == "Run_Line":
        # Spread — `line_value` is the team-side spread (negative for
        # favourite). For home -1.5 we need P(home_margin > 1.5).
        line_value = line.line_value if line.line_value is not None else -1.5
        is_home = bool(line.team_tricode and
                          line.team_tricode == line.home_tricode)
        # Run-line cover threshold: we want P(team's_margin > line_value).
        # If line_value=-1.5 (favoured), we need margin > 1.5 (covered).
        # If line_value=+1.5 (dog), we need margin > -1.5 (lose by ≤1).
        threshold = -float(line_value)
        if is_home:
            prob = _skellam_p_diff_gt(threshold, lam_home, lam_away)
        else:
            prob = _skellam_p_diff_gt(threshold, lam_away, lam_home)
        lam_used = lam_total
    else:
        # Unknown market — defensive fallback.
        prob = 0.5
        lam_used = lam_total

    n_h = home_rates.n_games if home_rates else 0
    n_a = away_rates.n_games if away_rates else 0
    confidence = _confidence_for_blend(min(n_h, n_a), knobs.prior_weight_games)

    return ProjectedFullGameSide(
        market=line.market, side=line.side, line_value=line.line_value,
        model_prob=float(max(0.0, min(1.0, prob))),
        confidence=float(confidence),
        lam_home=float(lam_home), lam_away=float(lam_away),
        lam_used=float(lam_used),
        blend_n_home=n_h, blend_n_away=n_a,
    )


def project_all(
    lines: Iterable[FullGameLine], *,
    rates_by_team: Optional[dict[str, TeamRollingRates]] = None,
    knobs: Optional[ProjectionKnobs] = None,
) -> list[ProjectedFullGameSide]:
    """Project every line in `lines` using `rates_by_team` (keyed on
    tricode). When `rates_by_team` is None, every projection uses the
    league prior."""
    rates_by_team = rates_by_team or {}
    out: list[ProjectedFullGameSide] = []
    for line in lines:
        home_rates = rates_by_team.get(line.home_tricode) if line.home_tricode else None
        away_rates = rates_by_team.get(line.away_tricode) if line.away_tricode else None
        out.append(project_full_game_market(
            line, home_rates=home_rates, away_rates=away_rates, knobs=knobs,
        ))
    return out
