"""
MLB Stats API results ingestor.

Mirror of TheSportsDBResultsIngestor that targets statsapi.mlb.com
instead of TheSportsDB. Used for MLB only -- TheSportsDB stays the
source for NBA / NHL / KBO / NPB / EPL / UCL / NFL until we build
per-sport equivalents for those.

Same output shape (GameResult written to GameResultsStore.record),
same idempotency (UNIQUE on game_id), same audit summary
(IngestSummary). Drop-in compatible with the existing backfill /
auto-settle CLI plumbing -- only the source class changes.

Game-id strategy: MLB Stats API exposes a stable per-game integer
called `gamePk` (e.g. 778911). We prefix it ("MLB-STATS-778911") so
the unique constraint on game_results.game_id can't collide with a
TheSportsDB-sourced MLB row (which uses TheSportsDB's idEvent like
"2078123"). If both sources accidentally run, you get two rows for
the same game with different ids -- visible and easy to clean up
rather than silently corrupting via key collision.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Optional

from edge_equation.stats.mlb_stats_client import MlbStatsClient
from edge_equation.stats.results import GameResult, GameResultsStore


# MLB Stats API status codes that indicate a game is final / settled.
# codedGameState values: "F" = Final, "FR" = Final: Review, "FT" =
# Forfeit, "S" = Suspended, "I" = In Progress, "P" = Pre-game, etc.
# We accept anything that maps to a settled outcome with a final score.
_FINISHED_CODED_STATES = frozenset({"F", "FR", "FT"})
_FINISHED_ABSTRACT_STATES = frozenset({"Final"})


@dataclass(frozen=True)
class IngestSummary:
    """Per-run audit log -- mirrors TheSportsDB ingestor's shape so
    callers (CLI, workflows) can format output uniformly."""
    days_scanned: int
    leagues_scanned: int
    events_seen: int
    events_finished: int
    results_written: int
    skipped_no_scores: int
    skipped_non_final: int

    def to_dict(self) -> dict:
        return {
            "days_scanned": self.days_scanned,
            "leagues_scanned": self.leagues_scanned,
            "events_seen": self.events_seen,
            "events_finished": self.events_finished,
            "results_written": self.results_written,
            "skipped_no_scores": self.skipped_no_scores,
            "skipped_non_final": self.skipped_non_final,
        }


def _is_final(game: Dict[str, Any]) -> bool:
    status = game.get("status") or {}
    coded = status.get("codedGameState")
    if coded in _FINISHED_CODED_STATES:
        return True
    abstract = status.get("abstractGameState")
    return abstract in _FINISHED_ABSTRACT_STATES


def _parse_int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None


def parse_game_as_result(game: Dict[str, Any]) -> Optional[GameResult]:
    """Map an MLB Stats API game dict -> GameResult, or None when the
    game isn't a settled-with-scores row.

    Defensive: missing fields / unparseable scores yield None so the
    ingestor counts skipped rows in skipped_no_scores / skipped_
    non_final rather than crashing the run.
    """
    if not isinstance(game, dict):
        return None
    teams = game.get("teams") or {}
    home_block = teams.get("home") or {}
    away_block = teams.get("away") or {}
    home_team = ((home_block.get("team") or {}).get("name") or "").strip()
    away_team = ((away_block.get("team") or {}).get("name") or "").strip()
    if not home_team or not away_team:
        return None
    home_score = _parse_int(home_block.get("score"))
    away_score = _parse_int(away_block.get("score"))
    if home_score is None or away_score is None:
        return None
    if not _is_final(game):
        return None
    game_pk = game.get("gamePk")
    if game_pk is None:
        return None
    game_id = f"MLB-STATS-{game_pk}"
    start_time = str(game.get("gameDate") or "").strip()
    return GameResult(
        result_id=None,
        game_id=game_id,
        league="MLB",
        home_team=home_team,
        away_team=away_team,
        start_time=start_time,
        home_score=home_score,
        away_score=away_score,
        status="final",
    )


class MlbStatsResultsIngestor:
    """
    Pulls MLB game results from MLB Stats API for one day or a day
    range and writes finished-game results into GameResultsStore.

    - ingest_day(conn, day, client=None) -> IngestSummary
    - backfill(conn, days=30, end_day=None, client=None) -> IngestSummary
    """

    @staticmethod
    def ingest_day(
        conn: sqlite3.Connection,
        day: date,
        client: Optional[MlbStatsClient] = None,
    ) -> IngestSummary:
        """Pull every MLB game for `day`, parse, write finalized ones
        to GameResultsStore. Returns an audit summary."""
        owns_client = client is None
        sdb = client or MlbStatsClient()
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            games = sdb.schedule_for_date(conn, day=day)
            for game in games or []:
                events_seen += 1
                result = parse_game_as_result(game)
                if result is None:
                    teams = game.get("teams") or {}
                    home_score = (teams.get("home") or {}).get("score")
                    away_score = (teams.get("away") or {}).get("score")
                    if home_score is None or away_score is None:
                        skipped_no_scores += 1
                    else:
                        skipped_non_final += 1
                    continue
                events_finished += 1
                GameResultsStore.record(conn, result)
                results_written += 1
        finally:
            if owns_client:
                sdb.close()
        return IngestSummary(
            days_scanned=1,
            leagues_scanned=1,  # MLB only
            events_seen=events_seen,
            events_finished=events_finished,
            results_written=results_written,
            skipped_no_scores=skipped_no_scores,
            skipped_non_final=skipped_non_final,
        )

    @staticmethod
    def backfill(
        conn: sqlite3.Connection,
        days: int = 30,
        end_day: Optional[date] = None,
        client: Optional[MlbStatsClient] = None,
    ) -> IngestSummary:
        """Walk `days` days back from end_day (default: today) and call
        ingest_day for each. Aggregates the per-day summaries into a
        single IngestSummary so the CLI can print one audit line.
        """
        if days < 1:
            return IngestSummary(0, 0, 0, 0, 0, 0, 0)
        owns_client = client is None
        sdb = client or MlbStatsClient()
        end = end_day or date.today()
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            for offset in range(days):
                day = end - timedelta(days=offset)
                s = MlbStatsResultsIngestor.ingest_day(conn, day, client=sdb)
                events_seen += s.events_seen
                events_finished += s.events_finished
                results_written += s.results_written
                skipped_no_scores += s.skipped_no_scores
                skipped_non_final += s.skipped_non_final
        finally:
            if owns_client:
                sdb.close()
        return IngestSummary(
            days_scanned=days,
            leagues_scanned=1,
            events_seen=events_seen,
            events_finished=events_finished,
            results_written=results_written,
            skipped_no_scores=skipped_no_scores,
            skipped_non_final=skipped_non_final,
        )
