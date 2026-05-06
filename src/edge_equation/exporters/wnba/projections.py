"""
WNBA game-level projection model — Normal-CDF based.

Why simpler than MLB's NegBin: basketball team totals are not strongly
over-dispersed. Empirical SD on team scoring across 6 seasons is ~10
points on a mean of ~80 (variance ~100, ratio 1.25), well-behaved
under a Normal approximation. So instead of NegBin / Poisson the
projector uses logistic on margin (for ML / spread cover) and Normal
CDF on totals.

Methodology — per-team aggregates:

    proj_team_pts = SEASON_W * team_pts_pg
                  + RECENT_W * team_recent_pts_pg
                  + OPP_W    * opponent_pts_against_pg

Decay-weighted season aggregate (14-day half life) so a team that was
hot a month ago doesn't drag a current cold streak. Hard last-N window
(default 10 games) for very-fresh-form sensitivity.

Win probability / spread cover via logistic on projected margin.
Total over/under via Normal CDF on projected total.

The ProjectionModel constructor accepts a `calibration` dict that
overrides default SDs and the win-prob slope with values fitted from
the rolling backtest residuals. The daily orchestrator passes the
backtest's calibration block in on every run, so SDs track the
season's actual variance instead of drifting against hardcoded
values. Same pattern as MLB.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime


SEASON_WEIGHT = 0.45
RECENT_WEIGHT = 0.30
OPPONENT_WEIGHT = 0.25
RECENT_WINDOW = 10

# League averages — calibrated to the 2021-2025 backfill mean. Real
# numbers for that 5-season window: team_pts_pg ≈ 81, total ≈ 162.
LEAGUE_AVG_PTS_PER_TEAM = 81.0
LEAGUE_AVG_FIRST_HALF_PTS_PER_TEAM = 40.5

# Bayesian shrinkage: pseudo-count of league-average "ghost games"
# mixed into every team's running totals. Larger k = more pull toward
# league mean (better for early-season noise / cold-start).
SHRINKAGE_K = 8

# Phase 2: exponential decay on the SEASON aggregate so recent games
# count more than ones from a month ago.
DEFAULT_DECAY_HALF_LIFE_DAYS = 14.0

# Default win-probability slope (logistic on projected margin). Real
# WNBA: ~0.10 win-rate per point of margin in expectation. The slope
# gets re-fit from backtest residuals when calibration is supplied.
WIN_PROB_SLOPE = 0.45

# Standard deviations for deriving probabilities from point projections.
# Defaults from typical WNBA game-to-game variance; backtest overrides.
TEAM_PTS_SD = 10.0    # one team's points
TOTAL_SD = 14.0       # full-game total
MARGIN_SD = 12.0      # full-game margin
FIRST_HALF_TOTAL_SD = 8.0


def _logistic(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erf — no scipy dependency."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def prob_over(line: float, mean: float, sd: float) -> float:
    """P(X > line) for X ~ Normal(mean, sd^2). Used for over/under
    totals + spread cover probabilities."""
    if sd <= 0:
        return 1.0 if mean > line else 0.0
    return 1.0 - _norm_cdf((line - mean) / sd)


def prob_total_over_under(line: float, mean: float, sd: float) -> tuple[float, float, float]:
    """(P(over), P(under), P(push)) for a Normal-distributed total at
    the given betting line. Half-point lines (e.g. 165.5) have
    P(push)=0; whole-number lines bake the push slot in via a small
    interval centered on the line.
    """
    p_over = prob_over(line, mean, sd)
    is_whole = abs(line - round(line)) < 1e-9
    if is_whole:
        # Treat push as a 1-point band [line - 0.5, line + 0.5] for the
        # discrete-points approximation. Tiny correction; only relevant
        # at integer lines.
        p_push = max(0.0, prob_over(line - 0.5, mean, sd) - p_over)
        p_under = 1.0 - p_over - p_push
    else:
        p_push = 0.0
        p_under = 1.0 - p_over
    return p_over, max(0.0, p_under), p_push


class ProjectionModel:
    """Aggregates per-team stats and projects matchup outcomes for
    WNBA. Mirrors MLB's ProjectionModel API so the orchestrator can
    delegate uniformly: calibration: dict | None, project_slate(slate),
    project_matchup(away, home).
    """

    def __init__(
        self,
        games: list[dict],
        shrinkage_k: int = SHRINKAGE_K,
        calibration: dict | None = None,
        decay_half_life_days: float = DEFAULT_DECAY_HALF_LIFE_DAYS,
    ):
        self.games = sorted(games, key=lambda g: g.get("date", ""))
        self.team_games: dict[str, list[dict]] = defaultdict(list)
        self.shrinkage_k = shrinkage_k
        self.decay_half_life_days = decay_half_life_days
        self._reference_date = self._latest_game_date()

        cal = calibration or {}
        self.team_pts_sd = cal.get("team_pts_sd", TEAM_PTS_SD)
        self.total_sd = cal.get("total_sd", TOTAL_SD)
        self.margin_sd = cal.get("margin_sd", MARGIN_SD)
        self.first_half_total_sd = cal.get(
            "first_half_total_sd", FIRST_HALF_TOTAL_SD,
        )
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
            away = g.get("away_team")
            home = g.get("home_team")
            if not (away and home):
                continue
            self.team_games[away].append(self._team_view(g, side="away"))
            self.team_games[home].append(self._team_view(g, side="home"))

    @staticmethod
    def _team_view(game: dict, side: str) -> dict:
        opp_side = "home" if side == "away" else "away"
        ps = game.get(f"{side}_score") or 0
        pa = game.get(f"{opp_side}_score") or 0
        h1 = game.get(f"{side}_1h") or 0
        h1_a = game.get(f"{opp_side}_1h") or 0
        return {
            "date": game.get("date"),
            "team": game.get(f"{side}_team"),
            "opponent": game.get(f"{opp_side}_team"),
            "side": side,
            "pts": ps,
            "pts_against": pa,
            "first_half_pts": h1,
            "first_half_pts_against": h1_a,
            "won": ps > pa,
        }

    def _aggregate(self, rows: list[dict], shrunk: bool = True,
                   weights: list[float] | None = None) -> dict:
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
                "pts_pg": LEAGUE_AVG_PTS_PER_TEAM,
                "pts_against_pg": LEAGUE_AVG_PTS_PER_TEAM,
                "first_half_pts_pg": LEAGUE_AVG_FIRST_HALF_PTS_PER_TEAM,
                "first_half_pts_against_pg": LEAGUE_AVG_FIRST_HALF_PTS_PER_TEAM,
                "win_pct": 0.5,
            }

        def w_sum_field(field: str) -> float:
            return sum(w * (r.get(field) or 0) for w, r in zip(weights, rows))

        def w_sum_pred(pred) -> float:
            return sum(w for w, r in zip(weights, rows) if pred(r))

        return {
            "n": n_raw,
            "n_eff": round(n_eff, 2),
            "pts_pg":
                (w_sum_field("pts") + k * LEAGUE_AVG_PTS_PER_TEAM) / denom,
            "pts_against_pg":
                (w_sum_field("pts_against") + k * LEAGUE_AVG_PTS_PER_TEAM) / denom,
            "first_half_pts_pg":
                (w_sum_field("first_half_pts")
                 + k * LEAGUE_AVG_FIRST_HALF_PTS_PER_TEAM) / denom,
            "first_half_pts_against_pg":
                (w_sum_field("first_half_pts_against")
                 + k * LEAGUE_AVG_FIRST_HALF_PTS_PER_TEAM) / denom,
            "win_pct":
                (w_sum_pred(lambda r: r["won"]) + k * 0.5) / denom,
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

    def project_matchup(self, away: str, home: str) -> dict:
        a = self.team_summary(away)
        h = self.team_summary(home)

        away_pts = self._blend(
            a["season"]["pts_pg"], a["recent"]["pts_pg"],
            h["season"]["pts_against_pg"],
        )
        home_pts = self._blend(
            h["season"]["pts_pg"], h["recent"]["pts_pg"],
            a["season"]["pts_against_pg"],
        )
        away_h1 = self._blend(
            a["season"]["first_half_pts_pg"], a["recent"]["first_half_pts_pg"],
            h["season"]["first_half_pts_against_pg"],
        )
        home_h1 = self._blend(
            h["season"]["first_half_pts_pg"], h["recent"]["first_half_pts_pg"],
            a["season"]["first_half_pts_against_pg"],
        )

        margin = home_pts - away_pts
        home_win_prob = _logistic(self.win_prob_slope * margin)
        away_win_prob = 1.0 - home_win_prob

        return {
            "away_team": away,
            "home_team": home,
            "away_pts_proj": round(away_pts, 2),
            "home_pts_proj": round(home_pts, 2),
            "total_proj": round(away_pts + home_pts, 2),
            "margin_proj": round(margin, 2),
            "ml_pick": home if home_win_prob >= 0.5 else away,
            "home_win_prob": round(home_win_prob, 3),
            "away_win_prob": round(away_win_prob, 3),
            "first_half_total_proj": round(away_h1 + home_h1, 2),
            "first_half_margin_proj": round(home_h1 - away_h1, 2),
            "model_meta": {
                "season_weight": SEASON_WEIGHT,
                "recent_weight": RECENT_WEIGHT,
                "opponent_weight": OPPONENT_WEIGHT,
                "recent_window": RECENT_WINDOW,
                "shrinkage_k": self.shrinkage_k,
                "team_pts_sd": self.team_pts_sd,
                "total_sd": self.total_sd,
                "margin_sd": self.margin_sd,
                "win_prob_slope": self.win_prob_slope,
                "calibrated": bool(self.calibration),
                "away_games_used": a["season"]["n"],
                "home_games_used": h["season"]["n"],
            },
        }

    def project_slate(self, slate: list[dict]) -> list[dict]:
        out = []
        for g in slate:
            if not (g.get("away_team") and g.get("home_team")):
                continue
            proj = self.project_matchup(g["away_team"], g["home_team"])
            proj["date"] = g.get("date")
            proj["game_id"] = g.get("game_id")
            proj["game_time"] = g.get("game_time")
            out.append(proj)
        return out
