"""
Projection model for MLB game-level bets — ported verbatim from
edge-equation-scrapers/exporters/mlb/projections.py.

Methodology: weighted blend of (season pace, last-10 form, opponent pace).
    proj_team_runs = w_season * team_season_RS_pg
                   + w_recent * team_recent_RS_pg
                   + w_opp    * opponent_RA_pg

Win probability is derived from projected margin via a logistic curve
calibrated to MLB's typical run-differential -> win-rate relationship
(roughly 0.10 per run of margin in expectation).

Negative Binomial parameterization (mean μ, dispersion r):
    mean      = μ
    variance  = μ + μ²/r
This addresses the regression cause v1 has hit: empirical run variance
exceeds Poisson's variance==mean assumption, producing overconfident
totals probabilities and inflated edges.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from edge_equation.exporters.mlb.park_factors import park_factor


SEASON_WEIGHT = 0.45
RECENT_WEIGHT = 0.30
OPPONENT_WEIGHT = 0.25
RECENT_WINDOW = 10

LEAGUE_AVG_RUNS_PER_TEAM = 4.5
LEAGUE_AVG_F1_RUNS_PER_TEAM = 0.55
LEAGUE_AVG_F5_RUNS_PER_TEAM = 2.4
LEAGUE_F1_SCORE_RATE = 0.27

SHRINKAGE_K = 15
DEFAULT_DECAY_HALF_LIFE_DAYS = 14.0

WIN_PROB_SLOPE = 0.45

TOTAL_SD = 3.0
TEAM_TOTAL_SD = 2.2
MARGIN_SD = 3.5
F5_TOTAL_SD = 2.2
F5_MARGIN_SD = 2.2


def _logistic(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def prob_over(line: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 1.0 if mean > line else 0.0
    return 1.0 - _norm_cdf((line - mean) / sd)


# ---- Poisson helpers -----------------------------------------------------

def poisson_pmf(k: int, lam: float) -> float:
    if k < 0 or lam <= 0:
        return 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0
    total = 0.0
    for i in range(k + 1):
        total += poisson_pmf(i, lam)
    return min(1.0, total)


def prob_over_under_poisson(line: float, lam: float) -> tuple[float, float, float]:
    if lam <= 0:
        return (0.0, 1.0, 0.0)
    threshold = math.floor(line)
    p_le_threshold = poisson_cdf(threshold, lam)
    p_over = 1.0 - p_le_threshold
    if abs(line - round(line)) < 1e-9:
        p_push = poisson_pmf(int(line), lam)
        p_under = p_le_threshold - p_push
    else:
        p_push = 0.0
        p_under = p_le_threshold
    return (p_over, p_under, p_push)


def prob_margin_atleast_poisson(threshold: int, lam_a: float, lam_b: float) -> float:
    if lam_a <= 0 and lam_b <= 0:
        return 1.0 if threshold <= 0 else 0.0
    y_max = max(20, int(lam_b + 8 * math.sqrt(lam_b)) + 5)
    total = 0.0
    for y in range(y_max + 1):
        p_y = poisson_pmf(y, lam_b)
        if p_y < 1e-12:
            continue
        x_min = y + threshold
        if x_min <= 0:
            p_x = 1.0
        else:
            p_x = 1.0 - poisson_cdf(x_min - 1, lam_a)
        total += p_y * p_x
    return min(1.0, max(0.0, total))


# ---- Negative Binomial helpers (over-dispersed counts) -------------------

def negbin_pmf(k: int, mu: float, r: float) -> float:
    if k < 0 or mu <= 0 or r <= 0:
        return 0.0
    log_pmf = (
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * math.log(r / (r + mu))
        + k * math.log(mu / (r + mu))
    )
    return math.exp(log_pmf)


def negbin_cdf(k: int, mu: float, r: float) -> float:
    if k < 0:
        return 0.0
    if mu <= 0:
        return 1.0
    total = 0.0
    for i in range(k + 1):
        total += negbin_pmf(i, mu, r)
    return min(1.0, total)


def dispersion_from_sd(mu: float, sd: float) -> float | None:
    if mu <= 0 or sd <= 0:
        return None
    variance = sd * sd
    if variance <= mu:
        return None
    return mu * mu / (variance - mu)


def prob_over_under_negbin(
    line: float, mu: float, r: float,
) -> tuple[float, float, float]:
    if mu <= 0 or r is None or r <= 0:
        return (0.0, 1.0, 0.0)
    threshold = math.floor(line)
    p_le_threshold = negbin_cdf(threshold, mu, r)
    p_over = 1.0 - p_le_threshold
    if abs(line - round(line)) < 1e-9:
        p_push = negbin_pmf(int(line), mu, r)
        p_under = p_le_threshold - p_push
    else:
        p_push = 0.0
        p_under = p_le_threshold
    return (p_over, p_under, p_push)


def prob_over_under_smart(
    line: float, mu: float, sd: float | None,
) -> tuple[float, float, float]:
    """Pick NegBin when calibrated SD implies over-dispersion; else Poisson."""
    if sd is None or sd <= 0:
        return prob_over_under_poisson(line, mu)
    r = dispersion_from_sd(mu, sd)
    if r is None:
        return prob_over_under_poisson(line, mu)
    return prob_over_under_negbin(line, mu, r)


class ProjectionModel:
    """Aggregates per-team stats and projects matchup outcomes."""

    def __init__(
        self,
        games: list[dict],
        shrinkage_k: int = SHRINKAGE_K,
        calibration: dict | None = None,
        apply_park_factors: bool = True,
        decay_half_life_days: float = DEFAULT_DECAY_HALF_LIFE_DAYS,
    ):
        self.games = sorted(games, key=lambda g: g.get("date", ""))
        self.team_games: dict[str, list[dict]] = defaultdict(list)
        self.shrinkage_k = shrinkage_k
        self.apply_park_factors = apply_park_factors
        self.decay_half_life_days = decay_half_life_days
        self._reference_date = self._latest_game_date()

        cal = calibration or {}
        self.total_sd = cal.get("total_sd", TOTAL_SD)
        self.team_total_sd = cal.get("team_total_sd", TEAM_TOTAL_SD)
        self.margin_sd = cal.get("margin_sd", MARGIN_SD)
        self.f5_total_sd = cal.get("f5_total_sd", F5_TOTAL_SD)
        self.f5_margin_sd = cal.get("f5_margin_sd", F5_MARGIN_SD)
        self.win_prob_slope = cal.get("win_prob_slope", WIN_PROB_SLOPE)
        self.calibration = cal

        self._build()

    def _latest_game_date(self) -> datetime | None:
        if not self.games:
            return None
        for g in reversed(self.games):
            d = g.get("date")
            if not d:
                continue
            try:
                return datetime.strptime(d, "%Y-%m-%d")
            except (TypeError, ValueError):
                continue
        return None

    def _decay_weights(self, rows: list[dict]) -> list[float]:
        if self.decay_half_life_days <= 0 or self._reference_date is None:
            return [1.0] * len(rows)
        weights: list[float] = []
        hl = self.decay_half_life_days
        for r in rows:
            d = r.get("date")
            if not d:
                weights.append(1.0)
                continue
            try:
                game_date = datetime.strptime(d, "%Y-%m-%d")
            except (TypeError, ValueError):
                weights.append(1.0)
                continue
            days_ago = max(0, (self._reference_date - game_date).days)
            weights.append(0.5 ** (days_ago / hl))
        return weights

    def _build(self) -> None:
        for g in self.games:
            away, home = g["away_team"], g["home_team"]
            self.team_games[away].append(self._team_view(g, side="away"))
            self.team_games[home].append(self._team_view(g, side="home"))

    @staticmethod
    def _team_view(game: dict, side: str) -> dict:
        opp_side = "home" if side == "away" else "away"
        rs = game[f"{side}_score"]
        ra = game[f"{opp_side}_score"]
        f1_rs = game[f"f1_{side}"]
        f1_ra = game[f"f1_{opp_side}"]
        f5_rs = game[f"f5_{side}"]
        f5_ra = game[f"f5_{opp_side}"]
        return {
            "date": game["date"],
            "team": game[f"{side}_team"],
            "opponent": game[f"{opp_side}_team"],
            "side": side,
            "rs": rs, "ra": ra,
            "f1_rs": f1_rs, "f1_ra": f1_ra,
            "f5_rs": f5_rs, "f5_ra": f5_ra,
            "scored_in_f1": f1_rs > 0,
            "allowed_in_f1": f1_ra > 0,
            "won": rs > ra,
        }

    def _aggregate(
        self,
        rows: list[dict],
        shrunk: bool = True,
        weights: list[float] | None = None,
    ) -> dict:
        n_raw = len(rows)
        if weights is None:
            weights = [1.0] * n_raw
        elif len(weights) != n_raw:
            raise ValueError("weights length must match rows length")

        n_eff = sum(weights)
        k = self.shrinkage_k if shrunk else 0
        denom = n_eff + k

        if denom == 0:
            return {
                "n": 0, "n_eff": 0.0,
                "rs_pg": LEAGUE_AVG_RUNS_PER_TEAM,
                "ra_pg": LEAGUE_AVG_RUNS_PER_TEAM,
                "f1_rs_pg": LEAGUE_AVG_F1_RUNS_PER_TEAM,
                "f1_ra_pg": LEAGUE_AVG_F1_RUNS_PER_TEAM,
                "f5_rs_pg": LEAGUE_AVG_F5_RUNS_PER_TEAM,
                "f5_ra_pg": LEAGUE_AVG_F5_RUNS_PER_TEAM,
                "f1_score_rate": LEAGUE_F1_SCORE_RATE,
                "f1_allow_rate": LEAGUE_F1_SCORE_RATE,
                "win_pct": 0.5,
            }

        def w_sum_field(field: str) -> float:
            return sum(w * r[field] for w, r in zip(weights, rows))

        def w_sum_pred(pred) -> float:
            return sum(w for w, r in zip(weights, rows) if pred(r))

        return {
            "n": n_raw, "n_eff": round(n_eff, 2),
            "rs_pg":   (w_sum_field("rs") + k * LEAGUE_AVG_RUNS_PER_TEAM) / denom,
            "ra_pg":   (w_sum_field("ra") + k * LEAGUE_AVG_RUNS_PER_TEAM) / denom,
            "f1_rs_pg":(w_sum_field("f1_rs") + k * LEAGUE_AVG_F1_RUNS_PER_TEAM) / denom,
            "f1_ra_pg":(w_sum_field("f1_ra") + k * LEAGUE_AVG_F1_RUNS_PER_TEAM) / denom,
            "f5_rs_pg":(w_sum_field("f5_rs") + k * LEAGUE_AVG_F5_RUNS_PER_TEAM) / denom,
            "f5_ra_pg":(w_sum_field("f5_ra") + k * LEAGUE_AVG_F5_RUNS_PER_TEAM) / denom,
            "f1_score_rate": (w_sum_pred(lambda r: r["scored_in_f1"]) + k * LEAGUE_F1_SCORE_RATE) / denom,
            "f1_allow_rate": (w_sum_pred(lambda r: r["allowed_in_f1"]) + k * LEAGUE_F1_SCORE_RATE) / denom,
            "win_pct":       (w_sum_pred(lambda r: r["won"]) + k * 0.5) / denom,
        }

    def team_summary(self, team: str) -> dict:
        rows = self.team_games.get(team, [])
        season_weights = self._decay_weights(rows)
        season = self._aggregate(rows, weights=season_weights)
        recent = self._aggregate(rows[-RECENT_WINDOW:])
        return {"team": team, "season": season, "recent": recent}

    @staticmethod
    def _blend(season: float, recent: float, opp: float) -> float:
        return (
            SEASON_WEIGHT * season
            + RECENT_WEIGHT * recent
            + OPPONENT_WEIGHT * opp
        )

    def project_matchup(
        self,
        away: str, home: str,
        away_sp: dict | None = None, home_sp: dict | None = None,
        away_bp: dict | None = None, home_bp: dict | None = None,
        weather: dict | None = None,
        away_lineup: dict | None = None, home_lineup: dict | None = None,
    ) -> dict:
        a = self.team_summary(away)
        h = self.team_summary(home)

        away_runs = self._blend(a["season"]["rs_pg"], a["recent"]["rs_pg"], h["season"]["ra_pg"])
        home_runs = self._blend(h["season"]["rs_pg"], h["recent"]["rs_pg"], a["season"]["ra_pg"])

        away_f5 = self._blend(a["season"]["f5_rs_pg"], a["recent"]["f5_rs_pg"], h["season"]["f5_ra_pg"])
        home_f5 = self._blend(h["season"]["f5_rs_pg"], h["recent"]["f5_rs_pg"], a["season"]["f5_ra_pg"])

        away_sp_factor = (away_sp or {}).get("factor", 1.0)
        home_sp_factor = (home_sp or {}).get("factor", 1.0)
        away_bp_factor = (away_bp or {}).get("factor", 1.0)
        home_bp_factor = (home_bp or {}).get("factor", 1.0)

        SP_SHARE = 5.0 / 9.0
        BP_SHARE = 4.0 / 9.0
        F5_SP_SHARE = 0.90
        F5_BP_SHARE = 0.10

        away_runs *= SP_SHARE * home_sp_factor + BP_SHARE * home_bp_factor
        home_runs *= SP_SHARE * away_sp_factor + BP_SHARE * away_bp_factor
        away_f5 *= F5_SP_SHARE * home_sp_factor + F5_BP_SHARE * home_bp_factor
        home_f5 *= F5_SP_SHARE * away_sp_factor + F5_BP_SHARE * away_bp_factor

        pf = park_factor(home) if self.apply_park_factors else 1.0
        away_runs *= pf
        home_runs *= pf
        away_f5 *= pf
        home_f5 *= pf

        wf = (weather or {}).get("factor", 1.0)
        wf_f5 = 1.0 + (wf - 1.0) * 0.5
        away_runs *= wf
        home_runs *= wf
        away_f5 *= wf_f5
        home_f5 *= wf_f5

        a_lineup_factor = (away_lineup or {}).get("factor", 1.0)
        h_lineup_factor = (home_lineup or {}).get("factor", 1.0)
        away_runs *= a_lineup_factor
        home_runs *= h_lineup_factor
        away_f5 *= a_lineup_factor
        home_f5 *= h_lineup_factor

        away_f1 = self._blend(a["season"]["f1_rs_pg"], a["recent"]["f1_rs_pg"], h["season"]["f1_ra_pg"])
        home_f1 = self._blend(h["season"]["f1_rs_pg"], h["recent"]["f1_rs_pg"], a["season"]["f1_ra_pg"])

        away_f1_score_p = self._blend(
            a["season"]["f1_score_rate"], a["recent"]["f1_score_rate"], h["season"]["f1_allow_rate"],
        )
        home_f1_score_p = self._blend(
            h["season"]["f1_score_rate"], h["recent"]["f1_score_rate"], a["season"]["f1_allow_rate"],
        )
        away_f1_score_p = max(0.0, min(1.0, away_f1_score_p))
        home_f1_score_p = max(0.0, min(1.0, home_f1_score_p))
        nrfi_prob = (1 - away_f1_score_p) * (1 - home_f1_score_p)

        margin = home_runs - away_runs
        home_win_prob = _logistic(self.win_prob_slope * margin)
        away_win_prob = 1.0 - home_win_prob

        # Run-line: bet the projected UNDERDOG at +1.5.
        if margin >= 0:
            fav_cover_prob = prob_over(1.5, margin, self.margin_sd)
            rl_fav_team = home
            rl_underdog_team = away
        else:
            fav_cover_prob = prob_over(1.5, -margin, self.margin_sd)
            rl_fav_team = away
            rl_underdog_team = home
        rl_cover_prob = 1.0 - fav_cover_prob

        f5_margin = home_f5 - away_f5
        if f5_margin > 0:
            f5_win_prob = 1 - _norm_cdf(-f5_margin / self.f5_margin_sd)
        elif f5_margin < 0:
            f5_win_prob = 1 - _norm_cdf(f5_margin / self.f5_margin_sd)
        else:
            f5_win_prob = 0.5

        return {
            "away_team": away, "home_team": home,
            "away_runs_proj": round(away_runs, 2),
            "home_runs_proj": round(home_runs, 2),
            "total_proj": round(away_runs + home_runs, 2),
            "margin_proj": round(margin, 2),
            "ml_pick": home if home_win_prob >= 0.5 else away,
            "home_win_prob": round(home_win_prob, 3),
            "away_win_prob": round(away_win_prob, 3),
            "rl_fav": rl_fav_team,
            "rl_pick": rl_underdog_team,
            "rl_pick_point": 1.5,
            "rl_margin_proj": round(abs(margin), 2),
            "rl_fav_covers_1_5": abs(margin) >= 1.5,
            "rl_fav_cover_prob": round(fav_cover_prob, 3),
            "rl_cover_prob": round(rl_cover_prob, 3),
            "f5_away_proj": round(away_f5, 2),
            "f5_home_proj": round(home_f5, 2),
            "f5_total_proj": round(away_f5 + home_f5, 2),
            "f5_pick": (
                home if home_f5 > away_f5
                else away if away_f5 > home_f5
                else "PUSH"
            ),
            "f5_win_prob": round(f5_win_prob, 3),
            "f1_away_proj": round(away_f1, 2),
            "f1_home_proj": round(home_f1, 2),
            "f1_total_proj": round(away_f1 + home_f1, 2),
            "nrfi_prob": round(nrfi_prob, 3),
            "yrfi_prob": round(1 - nrfi_prob, 3),
            "nrfi_pick": "NRFI" if nrfi_prob >= 0.5 else "YRFI",
            "away_total_proj": round(away_runs, 2),
            "home_total_proj": round(home_runs, 2),
            "model_meta": {
                "season_weight": SEASON_WEIGHT,
                "recent_weight": RECENT_WEIGHT,
                "opponent_weight": OPPONENT_WEIGHT,
                "recent_window": RECENT_WINDOW,
                "shrinkage_k": self.shrinkage_k,
                "park_factor": pf, "weather_factor": wf,
                "away_sp_factor": away_sp_factor, "home_sp_factor": home_sp_factor,
                "away_bp_factor": away_bp_factor, "home_bp_factor": home_bp_factor,
                "away_lineup_factor": a_lineup_factor, "home_lineup_factor": h_lineup_factor,
                "total_sd": self.total_sd, "margin_sd": self.margin_sd,
                "win_prob_slope": self.win_prob_slope,
                "calibrated": bool(self.calibration),
                "away_games_used": a["season"]["n"],
                "home_games_used": h["season"]["n"],
            },
        }

    def project_slate(self, slate: list[dict]) -> list[dict]:
        out = []
        for g in slate:
            proj = self.project_matchup(
                g["away_team"], g["home_team"],
                away_sp=g.get("away_sp"), home_sp=g.get("home_sp"),
                away_bp=g.get("away_bp"), home_bp=g.get("home_bp"),
                weather=g.get("weather"),
                away_lineup=g.get("away_lineup"), home_lineup=g.get("home_lineup"),
            )
            proj["date"] = g.get("date")
            proj["game_pk"] = g.get("game_pk")
            proj["game_time"] = g.get("game_time")
            out.append(proj)
        return out
