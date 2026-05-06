"""
WNBA rolling backtest — per-market ROI / Brier / hit_rate over the
historical games.json sample.

Walks games chronologically; at each step the projector sees only
prior games (no look-ahead). Grades the model's ML, spread (-3.5),
and total (over) calls against the actual outcomes. Aggregates by
bet_type to produce the summary the gate consumes.

Calibration block: fits SDs from squared residuals so the projector's
Normal/logistic distributions match the season's actual variance,
plus a one-parameter logistic slope on (margin, won) pairs. Same
shape as MLB's calibration so gate / orchestrator code can be shared.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from edge_equation.exporters.wnba.projections import (
    ProjectionModel,
    prob_over,
    prob_total_over_under,
    TEAM_PTS_SD,
    TOTAL_SD,
    MARGIN_SD,
)


FLAT_DECIMAL_ODDS = 1.909  # -110
MIN_BACKFILL_GAMES = 50    # don't bet until model has some signal

# Standard betting lines for backtesting purposes. Real books vary
# game-to-game; for the backtest summary we pin to representative
# values so per-market results are interpretable.
SPREAD_LINE = 3.5
TOTAL_LINES = (160.5, 162.5, 164.5)


def _settle(decimal_odds: float, won: bool, push: bool = False) -> float:
    if push:
        return 0.0
    if won:
        return decimal_odds - 1.0
    return -1.0


def _fit_logistic_slope(pairs: list[tuple[float, int]], iters: int = 1500) -> float:
    """Pure-stdlib gradient descent fit on slope in y = sigmoid(slope*x).
    Pairs is (margin_proj, won_int_0_or_1). Falls back to default slope
    if the sample is too thin."""
    if len(pairs) < 50:
        return 0.45
    slope = 0.05
    lr = 0.005
    for _ in range(iters):
        grad = 0.0
        for x, y in pairs:
            xc = max(-30.0, min(30.0, slope * x))
            p = 1.0 / (1.0 + math.exp(-xc))
            grad += (p - y) * x
        grad /= len(pairs)
        slope -= lr * grad
        if abs(grad) < 1e-6:
            break
    return max(0.01, min(2.0, slope))


def _fit_sd(residuals: list[float]) -> float:
    if len(residuals) < 30:
        return 0.0
    mean = sum(residuals) / len(residuals)
    variance = sum((r - mean) ** 2 for r in residuals) / len(residuals)
    return math.sqrt(max(0.0, variance))


class BacktestEngine:
    """Game-by-game model backtest with no look-ahead."""

    def __init__(
        self,
        games: Iterable[dict],
        min_history: int = MIN_BACKFILL_GAMES,
        decimal_odds: float = FLAT_DECIMAL_ODDS,
    ):
        self.games = sorted(games, key=lambda g: g.get("date", ""))
        self.min_history = min_history
        self.decimal_odds = decimal_odds
        self._seasons_loaded: dict = {}

    @classmethod
    def from_multi_season(
        cls,
        backfill_dir: "Path | str",
        seasons: Iterable[int],
        current_season_games: Iterable[dict] | None = None,
        **kwargs,
    ) -> "BacktestEngine":
        backfill_dir = Path(backfill_dir)
        all_games: list[dict] = []
        loaded: dict = {}
        for season in seasons:
            path = backfill_dir / str(season) / "games.json"
            if not path.exists():
                continue
            try:
                games = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            all_games.extend(games)
            loaded[season] = len(games)
        if current_season_games:
            cur = list(current_season_games)
            all_games.extend(cur)
            loaded["current"] = len(cur)
        engine = cls(all_games, **kwargs)
        engine._seasons_loaded = loaded
        return engine

    def run(self) -> dict:
        bets: list[dict] = []
        residuals = {
            "total": [],         # actual - proj_total
            "team": [],          # per-team actual - proj
            "margin": [],        # actual_margin - proj_margin
            "first_half_total": [],
            "ml_pairs": [],      # (proj_margin_home_minus_away, won_home_0_or_1)
        }

        for i, game in enumerate(self.games):
            history = self.games[:i]
            if len(history) < self.min_history:
                continue
            try:
                model = ProjectionModel(history)
                proj = model.project_matchup(game["away_team"], game["home_team"])
            except Exception:
                continue

            actual_total = (game.get("away_score") or 0) + (game.get("home_score") or 0)
            actual_margin = (game.get("home_score") or 0) - (game.get("away_score") or 0)
            actual_h1_total = (
                (game.get("away_1h") or 0) + (game.get("home_1h") or 0)
            )
            home_won_int = 1 if actual_margin > 0 else 0

            residuals["total"].append(actual_total - proj["total_proj"])
            residuals["margin"].append(actual_margin - proj["margin_proj"])
            residuals["team"].append((game.get("away_score") or 0) - proj["away_pts_proj"])
            residuals["team"].append((game.get("home_score") or 0) - proj["home_pts_proj"])
            if actual_h1_total:
                residuals["first_half_total"].append(
                    actual_h1_total - proj["first_half_total_proj"]
                )
            residuals["ml_pairs"].append((proj["margin_proj"], home_won_int))

            bets.extend(self._grade_all(proj, game))

        # Calibrate SDs + slope from residuals
        cal = self._calibration(residuals)

        summary = self._summarize(bets)
        overall = self._summarize_overall(bets)

        first_date = self.games[0].get("date", "") if self.games else None
        last_date = self.games[-1].get("date", "") if self.games else None

        return {
            "as_of": datetime_now_iso(),
            "first_date": first_date,
            "last_date": last_date,
            "total_games_in_window": len(self.games),
            "summary_by_bet_type": summary,
            "overall": overall,
            "calibration": cal,
            "bets": bets,
        }

    def _calibration(self, residuals: dict) -> dict:
        cal = {
            "team_pts_sd": _fit_sd(residuals["team"]) or TEAM_PTS_SD,
            "total_sd": _fit_sd(residuals["total"]) or TOTAL_SD,
            "margin_sd": _fit_sd(residuals["margin"]) or MARGIN_SD,
            "first_half_total_sd":
                _fit_sd(residuals["first_half_total"]) or 8.0,
            "win_prob_slope": _fit_logistic_slope(residuals["ml_pairs"]),
            "n_residuals": len(residuals["total"]),
        }
        # Round for log readability
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in cal.items()}

    def _grade_all(self, proj: dict, game: dict) -> list[dict]:
        out: list[dict] = []
        actual_total = (game.get("away_score") or 0) + (game.get("home_score") or 0)
        actual_margin = (game.get("home_score") or 0) - (game.get("away_score") or 0)
        date = game.get("date") or ""

        # Moneyline (home perspective): pick the projected favorite,
        # grade against actual ML winner.
        ml_pick_home = proj["home_win_prob"] >= 0.5
        ml_pick = proj["home_team"] if ml_pick_home else proj["away_team"]
        ml_won = (actual_margin > 0) if ml_pick_home else (actual_margin < 0)
        out.append({
            "date": date, "bet_type": "moneyline",
            "pick": ml_pick,
            "model_prob": (proj["home_win_prob"] if ml_pick_home
                            else proj["away_win_prob"]),
            "result": "WIN" if ml_won else ("PUSH" if actual_margin == 0 else "LOSS"),
            "units": _settle(self.decimal_odds, ml_won, actual_margin == 0),
        })

        # Spread: take the projected favorite at -SPREAD_LINE.
        # P(fav covers) = P(margin >= ceil(SPREAD_LINE)) ≈
        # 1 - Normal CDF(SPREAD_LINE | proj_margin, margin_sd).
        fav_is_home = proj["margin_proj"] >= 0
        fav_cover_prob = prob_over(
            SPREAD_LINE, abs(proj["margin_proj"]), MARGIN_SD,
        )
        # We bet the projected UNDERDOG at +SPREAD_LINE (cleaner side
        # historically; mirrors MLB's run-line treatment).
        underdog_team = proj["away_team"] if fav_is_home else proj["home_team"]
        # Underdog covers iff actual margin (fav perspective) is < SPREAD_LINE
        actual_fav_margin = abs(actual_margin) if (
            (actual_margin >= 0) == fav_is_home
        ) else -abs(actual_margin)
        underdog_covers = actual_fav_margin < SPREAD_LINE
        push = abs(actual_fav_margin - SPREAD_LINE) < 1e-9
        out.append({
            "date": date, "bet_type": "spread",
            "pick": f"{underdog_team} +{SPREAD_LINE}",
            "model_prob": round(1.0 - fav_cover_prob, 3),
            "result": "WIN" if underdog_covers else ("PUSH" if push else "LOSS"),
            "units": _settle(self.decimal_odds, underdog_covers, push),
        })

        # Totals: pick best Over/Under from the canonical line set.
        for line in TOTAL_LINES:
            p_over, p_under, _ = prob_total_over_under(
                line, proj["total_proj"], TOTAL_SD,
            )
            if p_over >= p_under:
                side = "OVER"
                won = actual_total > line
                push_total = actual_total == line
                prob = p_over
            else:
                side = "UNDER"
                won = actual_total < line
                push_total = actual_total == line
                prob = p_under
            out.append({
                "date": date, "bet_type": "totals",
                "pick": f"{side} {line}",
                "model_prob": round(prob, 3),
                "result": "WIN" if won else ("PUSH" if push_total else "LOSS"),
                "units": _settle(self.decimal_odds, won, push_total),
            })
            # Only one canonical totals row per game — break after the
            # first line so we don't triple-count the totals market in
            # the gate's bets count.
            break

        return out

    def _summarize(self, bets: list[dict]) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for b in bets:
            groups[b["bet_type"]].append(b)
        out = []
        for bt, lst in groups.items():
            wins = sum(1 for b in lst if b["result"] == "WIN")
            losses = sum(1 for b in lst if b["result"] == "LOSS")
            pushes = sum(1 for b in lst if b["result"] == "PUSH")
            decided = wins + losses
            roi_pct = round(
                sum(b["units"] for b in lst) / max(1, len(lst)) * 100, 2
            )
            hit_rate = round(wins / max(1, decided), 4) if decided else None
            brier_acc = sum(
                (b["model_prob"] - (1.0 if b["result"] == "WIN" else 0.0)) ** 2
                for b in lst if b["result"] in ("WIN", "LOSS") and b["model_prob"] is not None
            )
            brier_n = sum(1 for b in lst if b["result"] in ("WIN", "LOSS"))
            brier = round(brier_acc / brier_n, 4) if brier_n else None
            out.append({
                "bet_type": bt,
                "bets": len(lst),
                "wins": wins, "losses": losses, "pushes": pushes,
                "hit_rate": hit_rate, "roi_pct": roi_pct, "brier": brier,
            })
        return sorted(out, key=lambda r: r["bet_type"])

    def _summarize_overall(self, bets: list[dict]) -> dict:
        wins = sum(1 for b in bets if b["result"] == "WIN")
        losses = sum(1 for b in bets if b["result"] == "LOSS")
        units_pl = round(sum(b["units"] for b in bets), 2)
        decided = wins + losses
        return {
            "bets": len(bets),
            "wins": wins, "losses": losses,
            "hit_rate": round(wins / max(1, decided) * 100, 1) if decided else 0.0,
            "units_pl": units_pl,
        }


def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
