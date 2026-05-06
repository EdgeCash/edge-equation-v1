"""MLB Player-Props Historical Backtest.

Replays every game in the season backfill chronologically. For each
historical game:

  1. For each batter who saw a PA in that game, compute their per-PA
     rate for HR / Hits / Total_Bases / RBI from games BEFORE that
     date (no look-ahead).
  2. For each starting pitcher, compute their per-BF strikeout rate.
  3. Project each prop market line at flat -110 against the calibrated
     model probability.
  4. Grade against the actual stat line.
  5. Aggregate per-market summary + a play-only subset using the same
     `prob_floor_for` machinery the MLB game-results gate uses.

Inputs
------
``data/backfill/mlb/<season>/player_games.jsonl`` -- produced by
``scripts/backfill_player_games.py``.

Outputs
-------
A summary dict shaped exactly like ``BacktestEngine.run()`` so the
existing ``select_summary_for_gate`` machinery can consume it.

Why mirror BacktestEngine exactly?
----------------------------------
Once this exists, plugging the props gate into the BRAND_GUIDE
play-only path is one line. No bespoke gate consumer needed.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from edge_equation.engines.props_prizepicks.projection import (
    DEFAULT_PROPS_TEMPERATURE, calibrate_prob,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_BACKFILL_DIR = REPO_ROOT / "data" / "backfill" / "mlb"


FLAT_DECIMAL_ODDS = 1.909  # -110, the assumed historical price


# ---------------------------------------------------------------------
# Market lines we backtest. These mirror the lines the live engine
# offers via PrizePicks; the historical book pricing is unknowable
# without per-day odds snapshots, so we test at the same flat price the
# MLB game-results backtest uses.
# ---------------------------------------------------------------------

BATTER_LINES: Dict[str, Tuple[float, ...]] = {
    "HR":          (0.5,),
    "Hits":        (0.5, 1.5),
    "Total_Bases": (1.5, 2.5),
    "RBI":         (0.5, 1.5),
}

# Pitcher K lines vary widely; we keep two reasonable buckets and let
# the operator add more later.
PITCHER_LINES: Dict[str, Tuple[float, ...]] = {
    "K": (4.5, 5.5, 6.5),
}


# League priors -- per-PA / per-BF rates from a typical season. Used
# as the Bayesian prior when a player has no prior history (call-up,
# season opener). Shrinkage strength is `_PRIOR_WEIGHT_*`.
LEAGUE_BATTER_PRIOR_PER_PA = {
    "HR":          0.034,   # ~3.4% PAs end in HR for league
    "Hits":        0.230,
    "Total_Bases": 0.380,
    "RBI":         0.105,
}
LEAGUE_PITCHER_PRIOR_PER_BF = {
    "K": 0.235,
}

_PRIOR_WEIGHT_PA = 100.0   # ~25 games of regression
_PRIOR_WEIGHT_BF = 50.0    # ~2 starts of regression


def _bayesian_blend(
    obs: float, n: int, prior: float, prior_weight: float,
) -> float:
    if n <= 0:
        return prior
    return (obs * n + prior * prior_weight) / (n + prior_weight)


# ---------------------------------------------------------------------
# Poisson math (reused from the props engine but kept local to avoid
# a circular import on the projection module's market enum).
# ---------------------------------------------------------------------


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


def _settle(decimal_odds: float, won: bool, push: bool) -> float:
    if push:
        return 0.0
    if won:
        return round(decimal_odds - 1.0, 4)
    return -1.0


# ---------------------------------------------------------------------
# Per-player rolling-rate "time machine"
# ---------------------------------------------------------------------

@dataclass
class _BatterAgg:
    pa: int = 0
    hits: int = 0
    home_runs: int = 0
    total_bases: int = 0
    rbi: int = 0

    def rate(self, market: str) -> float:
        if self.pa <= 0:
            return 0.0
        if market == "HR":
            return self.home_runs / self.pa
        if market == "Hits":
            return self.hits / self.pa
        if market == "Total_Bases":
            return self.total_bases / self.pa
        if market == "RBI":
            return self.rbi / self.pa
        return 0.0


@dataclass
class _PitcherAgg:
    bf: int = 0
    strike_outs: int = 0

    def rate(self, market: str) -> float:
        if self.bf <= 0:
            return 0.0
        if market == "K":
            return self.strike_outs / self.bf
        return 0.0


@dataclass
class PlayerHistory:
    """Walks player_games.jsonl chronologically, exposing each player's
    cumulative stats *before* the current row. Lets us project each
    historical game with strictly prior data."""

    batter_agg: Dict[int, _BatterAgg] = field(default_factory=dict)
    pitcher_agg: Dict[int, _PitcherAgg] = field(default_factory=dict)
    player_names: Dict[int, str] = field(default_factory=dict)

    def batter_rate(self, player_id: int, market: str) -> Tuple[float, int]:
        agg = self.batter_agg.get(player_id)
        if agg is None:
            return 0.0, 0
        return agg.rate(market), agg.pa

    def pitcher_rate(self, player_id: int, market: str) -> Tuple[float, int]:
        agg = self.pitcher_agg.get(player_id)
        if agg is None:
            return 0.0, 0
        return agg.rate(market), agg.bf

    def update_with_row(self, row: Dict[str, Any]) -> None:
        """Add this row's outcomes to the player's history. Call AFTER
        projecting with the prior rate."""
        pid = row.get("player_id")
        if pid is None:
            return
        if row.get("player_name"):
            self.player_names[int(pid)] = row["player_name"]
        stats = row.get("stats") or {}
        if row.get("role") == "batter":
            agg = self.batter_agg.setdefault(int(pid), _BatterAgg())
            agg.pa          += int(stats.get("plateAppearances") or 0)
            agg.hits        += int(stats.get("hits") or 0)
            agg.home_runs   += int(stats.get("homeRuns") or 0)
            agg.total_bases += int(stats.get("totalBases") or 0)
            agg.rbi         += int(stats.get("rbi") or 0)
        elif row.get("role") == "pitcher":
            agg = self.pitcher_agg.setdefault(int(pid), _PitcherAgg())
            agg.bf          += int(stats.get("battersFaced") or 0)
            agg.strike_outs += int(stats.get("strikeOuts") or 0)


# ---------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------

def _iter_player_games(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("_error"):
                continue
            yield rec


def load_player_games(
    seasons: Iterable[int], backfill_dir: Path = DEFAULT_BACKFILL_DIR,
) -> List[Dict[str, Any]]:
    """Concat all per-season player_games.jsonl into one chronologically
    sorted list. Skips seasons whose JSONL is missing -- run the
    boxscore backfill first."""
    rows: List[Dict[str, Any]] = []
    for season in seasons:
        p = backfill_dir / str(season) / "player_games.jsonl"
        if not p.exists():
            continue
        for rec in _iter_player_games(p):
            rows.append(rec)
    # Sort by (date, game_pk, player_id) so updates are deterministic.
    rows.sort(key=lambda r: (
        r.get("date", ""),
        int(r.get("game_pk") or 0),
        int(r.get("player_id") or 0),
    ))
    return rows


# ---------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------

@dataclass
class PropsBacktestEngine:
    rows: List[Dict[str, Any]]
    decimal_odds: float = FLAT_DECIMAL_ODDS
    apply_calibration: bool = True
    expected_batter_pa: float = 4.1
    expected_pitcher_bf: float = 22.0
    min_history_pa: int = 30      # batter floor before grading
    min_history_bf: int = 30      # pitcher floor before grading

    def run(self) -> Dict[str, Any]:
        history = PlayerHistory()
        bets: List[Dict[str, Any]] = []
        for row in self.rows:
            pid = row.get("player_id")
            if pid is None:
                continue
            role = row.get("role")
            stats = row.get("stats") or {}
            if role == "batter":
                if int(stats.get("plateAppearances") or 0) <= 0:
                    history.update_with_row(row)
                    continue
                bets.extend(self._grade_batter(row, history))
            elif role == "pitcher" and row.get("started"):
                if int(stats.get("battersFaced") or 0) <= 0:
                    history.update_with_row(row)
                    continue
                bets.extend(self._grade_pitcher(row, history))
            history.update_with_row(row)

        # Build the play-only subset using the same prob_floor_for the
        # MLB game-results gate uses. The threshold dict is wider here
        # because props markets aren't in `gates.DEFAULT_EDGE_THRESHOLDS_BY_MARKET`;
        # we plumb them in via a helper.
        play_only_bets = [
            b for b in bets
            if b.get("model_prob") is not None
            and b["model_prob"] >= prob_floor_for_props(
                b["bet_type"], decimal_odds=self.decimal_odds,
            )
        ]
        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_player_game_rows": len(self.rows),
            "n_bets": len(bets),
            "n_play_only_bets": len(play_only_bets),
            "summary_by_bet_type": _summarize(bets),
            "summary_by_bet_type_play_only": _summarize(play_only_bets),
            "overall": _overall(bets),
            "overall_play_only": _overall(play_only_bets),
            "apply_calibration": self.apply_calibration,
        }

    # ------------------------------------------------------------------

    def _grade_batter(
        self, row: Dict[str, Any], history: PlayerHistory,
    ) -> List[Dict[str, Any]]:
        pid = int(row["player_id"])
        stats = row["stats"]
        out: List[Dict[str, Any]] = []
        for market, lines in BATTER_LINES.items():
            obs_rate, n_pa = history.batter_rate(pid, market)
            if n_pa < self.min_history_pa:
                continue
            prior = LEAGUE_BATTER_PRIOR_PER_PA.get(market, 0.0)
            blended = _bayesian_blend(obs_rate, n_pa, prior, _PRIOR_WEIGHT_PA)
            lam = blended * self.expected_batter_pa
            actual = self._actual_batter_count(market, stats)
            for line in lines:
                out.extend(self._grade_market_line(
                    bet_type=market, side="Over", line=line, lam=lam,
                    actual=actual, market_canonical=market,
                    player_name=row.get("player_name"),
                    matchup=f"{row.get('team')}@{row.get('opponent')}",
                    date=row.get("date"),
                ))
                out.extend(self._grade_market_line(
                    bet_type=market, side="Under", line=line, lam=lam,
                    actual=actual, market_canonical=market,
                    player_name=row.get("player_name"),
                    matchup=f"{row.get('team')}@{row.get('opponent')}",
                    date=row.get("date"),
                ))
        return out

    def _grade_pitcher(
        self, row: Dict[str, Any], history: PlayerHistory,
    ) -> List[Dict[str, Any]]:
        pid = int(row["player_id"])
        stats = row["stats"]
        out: List[Dict[str, Any]] = []
        for market, lines in PITCHER_LINES.items():
            obs_rate, n_bf = history.pitcher_rate(pid, market)
            if n_bf < self.min_history_bf:
                continue
            prior = LEAGUE_PITCHER_PRIOR_PER_BF.get(market, 0.0)
            blended = _bayesian_blend(obs_rate, n_bf, prior, _PRIOR_WEIGHT_BF)
            lam = blended * self.expected_pitcher_bf
            actual = self._actual_pitcher_count(market, stats)
            for line in lines:
                out.extend(self._grade_market_line(
                    bet_type=market, side="Over", line=line, lam=lam,
                    actual=actual, market_canonical=market,
                    player_name=row.get("player_name"),
                    matchup=f"{row.get('team')}@{row.get('opponent')}",
                    date=row.get("date"),
                ))
                out.extend(self._grade_market_line(
                    bet_type=market, side="Under", line=line, lam=lam,
                    actual=actual, market_canonical=market,
                    player_name=row.get("player_name"),
                    matchup=f"{row.get('team')}@{row.get('opponent')}",
                    date=row.get("date"),
                ))
        return out

    # ------------------------------------------------------------------

    def _grade_market_line(
        self, *, bet_type: str, side: str, line: float, lam: float,
        actual: int, market_canonical: str, player_name: Optional[str],
        matchup: str, date: Optional[str],
    ) -> List[Dict[str, Any]]:
        p_over = _prob_over_poisson(line, lam)
        prob = p_over if side == "Over" else 1.0 - p_over
        # We don't know the historical book line at -110, but the
        # implied probability there is 0.524. Using that as a stand-in
        # for `market_prob_devigged` lets us apply the same calibration
        # shrink the live engine applies. The model + market disagree
        # in opposite directions on Over/Under, so this preserves the
        # symmetric shrink behaviour the live engine has.
        market_prob_devigged = 1.0 / self.decimal_odds  # ~0.524
        if self.apply_calibration:
            prob_cal = calibrate_prob(
                model_prob=prob,
                market_prob_devigged=market_prob_devigged,
                market_canonical=market_canonical,
            )
        else:
            prob_cal = prob
        push = (math.floor(line) == line and actual == int(line))
        won = (
            (actual > line) if side == "Over"
            else (actual < line)
        ) if not push else False
        return [{
            "date": date,
            "matchup": matchup,
            "player": player_name,
            "bet_type": bet_type,
            "side": side,
            "line": line,
            "actual": actual,
            "lam": round(lam, 3),
            "model_prob_raw": round(prob, 4),
            "model_prob": round(prob_cal, 4),
            "result": "PUSH" if push else ("WIN" if won else "LOSS"),
            "units": _settle(self.decimal_odds, won, push),
        }]

    @staticmethod
    def _actual_batter_count(market: str, stats: Dict[str, Any]) -> int:
        if market == "HR":
            return int(stats.get("homeRuns") or 0)
        if market == "Hits":
            return int(stats.get("hits") or 0)
        if market == "Total_Bases":
            return int(stats.get("totalBases") or 0)
        if market == "RBI":
            return int(stats.get("rbi") or 0)
        return 0

    @staticmethod
    def _actual_pitcher_count(market: str, stats: Dict[str, Any]) -> int:
        if market == "K":
            return int(stats.get("strikeOuts") or 0)
        return 0


# ---------------------------------------------------------------------
# Per-market floor: same shape as gates.prob_floor_for, but using a
# props-specific edge floor map (lower than game results since props
# typically clear at a thinner margin).
# ---------------------------------------------------------------------

PROPS_EDGE_FLOOR_PCT: Dict[str, float] = {
    "HR":          5.0,
    "Hits":        4.0,
    "Total_Bases": 4.0,
    "RBI":         4.0,
    "K":           4.0,
}


def prob_floor_for_props(
    bet_type: str, decimal_odds: float = FLAT_DECIMAL_ODDS,
    overrides: Optional[Dict[str, float]] = None,
) -> float:
    """At decimal_odds, prob >= (1 + edge_pct/100) / decimal_odds."""
    base = PROPS_EDGE_FLOOR_PCT.get(bet_type, 4.0)
    floor_pct = (overrides or {}).get(bet_type, base)
    return (1.0 + floor_pct / 100.0) / decimal_odds


# ---------------------------------------------------------------------
# Aggregation helpers
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


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_equation.exporters.mlb.props_backtest",
        description="Replay player-prop history through the engine.",
    )
    parser.add_argument(
        "--seasons", type=int, nargs="+", required=True,
    )
    parser.add_argument(
        "--backfill-dir", type=Path, default=DEFAULT_BACKFILL_DIR,
    )
    parser.add_argument("--no-calibration", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Optional path to write the full summary JSON.",
    )
    args = parser.parse_args(argv)

    rows = load_player_games(args.seasons, args.backfill_dir)
    print(f"Loaded {len(rows):,} player-game rows from seasons {args.seasons}")
    if not rows:
        print("No data on disk. Run scripts/backfill_player_games.py first.")
        return 1

    engine = PropsBacktestEngine(
        rows=rows,
        apply_calibration=not args.no_calibration,
    )
    result = engine.run()

    print()
    print("=== ALL bets ===")
    print(f"{'market':<14}  {'bets':>6}  {'hit%':>5}  {'ROI%':>7}  {'Brier':>7}")
    for r in sorted(result["summary_by_bet_type"], key=lambda r: -r["roi_pct"]):
        print(
            f"{r['bet_type']:<14}  {r['bets']:>6}  {r['hit_rate']:>5.1f}  "
            f"{r['roi_pct']:>+7.2f}  {r['brier']!s:>7}"
        )
    print()
    print("=== PLAY-only (above edge floor) ===")
    print(f"{'market':<14}  {'bets':>6}  {'hit%':>5}  {'ROI%':>7}  {'Brier':>7}  Gate")
    for r in sorted(
        result["summary_by_bet_type_play_only"], key=lambda r: -r["roi_pct"],
    ):
        bri = r["brier"]
        passed = (
            r["bets"] >= 200 and r["roi_pct"] >= 1.0
            and bri is not None and bri < 0.246
        )
        flag = "PASS" if passed else "fail"
        print(
            f"{r['bet_type']:<14}  {r['bets']:>6}  {r['hit_rate']:>5.1f}  "
            f"{r['roi_pct']:>+7.2f}  {bri!s:>7}  {flag}"
        )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Strip the bets array from the on-disk JSON unless explicitly
        # requested -- the array dwarfs the summary and isn't usually
        # needed.
        slim = {k: v for k, v in result.items() if k not in ("bets",)}
        args.output.write_text(json.dumps(slim, indent=2, default=str))
        print(f"\nWrote summary to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
