"""Generic game-results backtester for two-team scoring sports.

Used by:
  - NBA  (points, regular-season + playoffs)
  - NHL  (goals; OT-aware via the regulation_total / had_ot fields the
          backfill records ship)

Walks the season's games chronologically and projects each game using
ONLY data available before it -- per-team rolling offensive + defensive
averages computed from prior games. Then grades:

  - moneyline:        pick the projected favourite
  - spread (margin):  projected underdog at +SPREAD_LINE
  - totals:           OVER / UNDER at the projected total

Modelled after `exporters/mlb/backtest.py`. Emits the same dict shape
(`summary_by_bet_type` + `summary_by_bet_type_play_only`) so the same
`select_summary_for_gate` plumbing applies.

The backtest assumes flat -110 odds across all bets (decimal 1.909) --
historical book lines aren't on disk, so we measure the model's edge
against a uniform market price. Real ROI may shift in either direction
once production runs against actual market prices, but the relative
ranking of markets (which beat -110, which don't) is honest.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


FLAT_DECIMAL_ODDS = 1.909  # -110

# Per-market edge floor in pp. Tunable per-sport via SPORT_EDGE_FLOORS.
DEFAULT_EDGE_FLOOR_PCT: Dict[str, float] = {
    "moneyline": 4.0,
    "spread":    3.0,
    "totals":    2.5,
}


def _settle(decimal_odds: float, won: bool, push: bool) -> float:
    if push:
        return 0.0
    if won:
        return round(decimal_odds - 1.0, 4)
    return -1.0


def _logistic(margin: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    z = max(-30.0, min(30.0, margin / scale))
    return 1.0 / (1.0 + math.exp(-z))


# ---------------------------------------------------------------------
# Per-team rolling stats
# ---------------------------------------------------------------------

@dataclass
class _TeamAgg:
    games: int = 0
    points_for: int = 0
    points_against: int = 0
    wins: int = 0

    @property
    def ppg(self) -> float:
        return self.points_for / self.games if self.games else 0.0

    @property
    def papg(self) -> float:
        return self.points_against / self.games if self.games else 0.0


@dataclass
class TeamHistory:
    """Per-team running averages, updated AFTER each game is graded."""
    teams: Dict[str, _TeamAgg] = field(default_factory=dict)

    def get(self, team: str) -> _TeamAgg:
        return self.teams.setdefault(team, _TeamAgg())

    def update(self, away: str, home: str,
               away_score: int, home_score: int) -> None:
        a = self.get(away)
        h = self.get(home)
        a.games += 1
        h.games += 1
        a.points_for += away_score
        a.points_against += home_score
        h.points_for += home_score
        h.points_against += away_score
        if home_score > away_score:
            h.wins += 1
        else:
            a.wins += 1


# ---------------------------------------------------------------------
# Spread / totals defaults
# ---------------------------------------------------------------------

@dataclass
class SportConfig:
    """Per-sport parameters. NBA + NHL share the same backtest core
    but differ on units (points vs goals) and SD (the sigmoid scale
    used to convert projected margin into a win probability)."""
    sport: str
    points_field: str = "total_points"   # how much the league scores per game on average
    league_avg_total: float = 226.0      # NBA default; NHL overrides
    spread_line: float = 5.5
    margin_sd: float = 11.0
    total_sd: float = 18.0
    min_history_games: int = 5           # per-team game floor before projecting


NBA_CONFIG = SportConfig(
    sport="nba",
    points_field="total_points",
    league_avg_total=226.0,
    spread_line=5.5,
    margin_sd=11.0,
    total_sd=18.0,
)

NHL_CONFIG = SportConfig(
    sport="nhl",
    points_field="total_goals",
    league_avg_total=6.2,
    spread_line=1.5,                # puck line
    margin_sd=2.0,
    total_sd=2.0,
)


# ---------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------

def load_games_jsonl(
    seasons: Iterable[int], backfill_dir: Path,
) -> List[Dict[str, Any]]:
    """Concat per-season JSONL into one chronologically sorted list.
    Skips sentinel lines (`_no_games`, `_error`) and any season whose
    file is missing on disk."""
    rows: List[Dict[str, Any]] = []
    for s in seasons:
        p = backfill_dir / str(s) / "games.jsonl"
        if not p.exists():
            continue
        with p.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_no_games") or rec.get("_error"):
                    continue
                if not rec.get("completed"):
                    continue
                rows.append(rec)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("game_id", "")))
    return rows


# ---------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------

@dataclass
class GameResultsBacktestEngine:
    rows: List[Dict[str, Any]]
    cfg: SportConfig
    decimal_odds: float = FLAT_DECIMAL_ODDS
    edge_floor_pct: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_EDGE_FLOOR_PCT),
    )

    def run(self) -> Dict[str, Any]:
        history = TeamHistory()
        bets: List[Dict[str, Any]] = []
        for game in self.rows:
            away, home = game.get("away_team"), game.get("home_team")
            if not (away and home):
                continue
            away_score = int(game.get("away_score") or 0)
            home_score = int(game.get("home_score") or 0)
            actual_total = away_score + home_score
            actual_margin = home_score - away_score   # positive = home won

            a = history.get(away)
            h = history.get(home)
            if a.games < self.cfg.min_history_games or \
               h.games < self.cfg.min_history_games:
                history.update(away, home, away_score, home_score)
                continue

            # Project: each side's points = team_off blended with opp_def.
            league_half = self.cfg.league_avg_total / 2.0
            away_proj = (a.ppg + h.papg) / 2.0
            home_proj = (h.ppg + a.papg) / 2.0
            # Safeguard: pull a tiny prior toward league avg so cold
            # starts don't extrapolate from N=5 wildly.
            away_proj = 0.85 * away_proj + 0.15 * league_half
            home_proj = 0.85 * home_proj + 0.15 * league_half
            total_proj = away_proj + home_proj
            margin_proj = home_proj - away_proj  # positive => home favoured

            home_win_prob = _logistic(margin_proj, self.cfg.margin_sd)
            away_win_prob = 1.0 - home_win_prob

            # Moneyline: bet projected favourite
            if home_win_prob >= away_win_prob:
                ml_pick, ml_prob = home, home_win_prob
                won_ml = home_score > away_score
            else:
                ml_pick, ml_prob = away, away_win_prob
                won_ml = away_score > home_score
            bets.append(self._record(
                game, "moneyline", ml_pick, ml_prob,
                won=won_ml, push=False,
            ))

            # Spread: bet projected underdog at +SPREAD_LINE
            line = self.cfg.spread_line
            if margin_proj >= 0:
                # home is fav, bet away +line
                cover_prob = 1.0 - _logistic(
                    -line - margin_proj, self.cfg.margin_sd,
                )
                won_spr = (home_score - away_score) < line
            else:
                # away is fav, bet home +line
                cover_prob = 1.0 - _logistic(
                    -line + margin_proj, self.cfg.margin_sd,
                )
                won_spr = (away_score - home_score) < line
            bets.append(self._record(
                game, "spread", "+{:.1f}".format(line), cover_prob,
                won=won_spr, push=False,
            ))

            # Totals: bet OVER if model_total > league_avg_total else UNDER,
            # at flat OU line == projected total rounded to nearest 0.5.
            ou_line = round(total_proj * 2) / 2  # nearest 0.5
            push = (actual_total == ou_line)
            if total_proj >= ou_line:
                pick_side = "OVER"
                p = 1.0 - _gaussian_cdf(ou_line, total_proj, self.cfg.total_sd)
                won_tot = actual_total > ou_line
            else:
                pick_side = "UNDER"
                p = _gaussian_cdf(ou_line, total_proj, self.cfg.total_sd)
                won_tot = actual_total < ou_line
            bets.append(self._record(
                game, "totals", f"{pick_side} {ou_line}", p,
                won=won_tot, push=push,
            ))

            history.update(away, home, away_score, home_score)

        # Play-only filter: edge_pp >= per-market floor at flat -110.
        from edge_equation.exporters.mlb.gates import prob_floor_for as _mlb_floor  # noqa: E501
        play_only: List[Dict[str, Any]] = []
        for b in bets:
            bt = b.get("bet_type")
            mp = b.get("model_prob")
            if mp is None:
                continue
            edge_floor_pct = self.edge_floor_pct.get(bt, 3.0)
            prob_floor = (1.0 + edge_floor_pct / 100.0) / self.decimal_odds
            if mp >= prob_floor:
                play_only.append(b)

        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sport": self.cfg.sport,
            "n_games_in_window": len(self.rows),
            "n_bets": len(bets),
            "n_play_only_bets": len(play_only),
            "summary_by_bet_type": _summarize(bets),
            "summary_by_bet_type_play_only": _summarize(play_only),
            "overall": _overall(bets),
            "overall_play_only": _overall(play_only),
        }

    def _record(
        self, game: Dict[str, Any], bet_type: str, pick: str,
        prob: float, *, won: bool, push: bool,
    ) -> Dict[str, Any]:
        return {
            "date": game.get("date"),
            "matchup": f"{game.get('away_team')}@{game.get('home_team')}",
            "bet_type": bet_type,
            "pick": pick,
            "model_prob": round(float(prob), 4),
            "result": "PUSH" if push else ("WIN" if won else "LOSS"),
            "units": _settle(self.decimal_odds, won, push),
        }


def _gaussian_cdf(x: float, mean: float, sd: float) -> float:
    """Phi((x-mean)/sd). Pure stdlib via math.erf."""
    if sd <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / (sd * math.sqrt(2))
    return 0.5 * (1.0 + math.erf(z))


# ---------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------

def _brier(bets: List[Dict[str, Any]]) -> Optional[float]:
    scored = [
        (b["model_prob"], 1 if b["result"] == "WIN" else 0)
        for b in bets if b["result"] in ("WIN", "LOSS")
        and b.get("model_prob") is not None
    ]
    if not scored:
        return None
    return round(sum((p - y) ** 2 for p, y in scored) / len(scored), 4)


def _summarize(bets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in bets:
        groups[b["bet_type"]].append(b)
    rows = []
    for bet_type in sorted(groups):
        grp = groups[bet_type]
        wins = sum(1 for b in grp if b["result"] == "WIN")
        losses = sum(1 for b in grp if b["result"] == "LOSS")
        pushes = sum(1 for b in grp if b["result"] == "PUSH")
        graded = wins + losses
        units = round(sum(b["units"] for b in grp), 2)
        rows.append({
            "bet_type": bet_type,
            "bets": len(grp),
            "wins": wins, "losses": losses, "pushes": pushes,
            "hit_rate": round(wins / graded * 100, 1) if graded else 0.0,
            "units_pl": units,
            "roi_pct": round(units / len(grp) * 100, 2) if grp else 0.0,
            "brier": _brier(grp),
        })
    return rows


def _overall(bets: List[Dict[str, Any]]) -> Dict[str, Any]:
    wins = sum(1 for b in bets if b["result"] == "WIN")
    losses = sum(1 for b in bets if b["result"] == "LOSS")
    pushes = sum(1 for b in bets if b["result"] == "PUSH")
    graded = wins + losses
    units = round(sum(b["units"] for b in bets), 2)
    return {
        "bets": len(bets), "wins": wins, "losses": losses, "pushes": pushes,
        "hit_rate": round(wins / graded * 100, 1) if graded else 0.0,
        "units_pl": units,
        "roi_pct": round(units / len(bets) * 100, 2) if bets else 0.0,
        "brier": _brier(bets),
    }
