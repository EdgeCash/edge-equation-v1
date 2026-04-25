"""
NBA Stats API results ingestor.

Mirror of MlbStatsResultsIngestor and NhleResultsIngestor that targets
stats.nba.com instead of MLB Stats API or NHL API. Used for NBA only.

Same output shape (GameResult written to GameResultsStore.record),
same idempotency (UNIQUE on game_id), same audit summary
(IngestSummary). Drop-in compatible with the existing backfill /
auto-settle CLI plumbing -- only the source class changes.

Game-id strategy: NBA Stats API exposes a stable per-game string
called `gameId` (e.g. "0021500001"). We prefix it ("NBA-STATS-0021500001")
so the unique constraint on game_results.game_id can't collide with a
TheSportsDB-sourced NBA row. If both sources accidentally run, you get
two rows for the same game with different ids -- visible and easy to
clean up rather than silently corrupting via key collision.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Optional

from edge_equation.stats.nba_stats_client import NbaStatsClient
from edge_equation.stats.results import GameResult, GameResultsStore


# NBA Stats API game status codes:
# 1 = Not Started, 2 = In Progress, 3 = Finished
_FINISHED_GAME_STATUS = 3


@dataclass(frozen=True)
class IngestSummary:
    """Per-run audit log -- mirrors MLB/NHL ingestor's shape so
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
    """Map an NBA Stats API game dict -> GameResult, or None when the
    game isn't a settled-with-scores row.

    Defensive: missing fields / unparseable scores yield None so the
    ingestor counts skipped rows in skipped_no_scores / skipped_
    non_final rather than crashing the run.
    """
    if not isinstance(game, dict):
        return None
    
    # Check game status
    game_status = game.get("gameStatus")
    if game_status != _FINISHED_GAME_STATUS:
        return None
    
    # Extract team info
    home_team = ((game.get("homeTeam") or {}).get("teamName") or "").strip()
    away_team = ((game.get("awayTeam") or {}).get("teamName") or "").strip()
    if not home_team or not away_team:
        return None
    
    # Extract scores
    home_score = _parse_int((game.get("homeTeam") or {}).get("score"))
    away_score = _parse_int((game.get("awayTeam") or {}).get("score"))
    if home_score is None or away_score is None:
        return None
    
    # Extract game ID
    game_id_raw = game.get("gameId")
    if game_id_raw is None:
        return None
    game_id = f"NBA-STATS-{game_id_raw}"
    
    # Extract start time (gameTimeUTC is ISO format)
    start_time = str(game.get("gameTimeUTC") or "").strip()
    
    return GameResult(
        result_id=None,
        game_id=game_id,
        league="NBA",
        home_team=home_team,
        away_team=away_team,
        start_time=start_time,
        home_score=home_score,
        away_score=away_score,
        status="final",
    )


class NbaStatsResultsIngestor:
    """
    Pulls NBA game results from NBA Stats API for one day or a day
    range and writes finished-game results into GameResultsStore.

    - ingest_day(conn, day, client=None) -> IngestSummary
    - backfill(conn, days=30, end_day=None, client=None) -> IngestSummary
    """

    @staticmethod
    def ingest_day(
        conn: sqlite3.Connection,
        day: date,
        client: Optional[NbaStatsClient] = None,
    ) -> IngestSummary:
        """Pull every NBA game for `day`, parse, write finalized ones
        to GameResultsStore. Returns an audit summary."""
        owns_client = client is None
        sdb = client or NbaStatsClient()
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            games = sdb.scoreboard_for_date(conn, day=day)
            for game in games or []:
                events_seen += 1
                result = parse_game_as_result(game)
                if result is None:
                    game_status = game.get("gameStatus")
                    home_score = _parse_int(
                        (game.get("homeTeam") or {}).get("score")
                    )
                    away_score = _parse_int(
                        (game.get("awayTeam") or {}).get("score")
                    )
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
            leagues_scanned=1,  # NBA only
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
        client: Optional[NbaStatsClient] = None,
    ) -> IngestSummary:
        """Walk `days` days back from end_day (default: today) and call
        ingest_day for each. Aggregates the per-day summaries into a
        single IngestSummary so the CLI can print one audit line.
        """
        if days < 1:
            return IngestSummary(0, 0, 0, 0, 0, 0, 0)
        owns_client = client is None
        sdb = client or NbaStatsClient()
        end = end_day or date.today()
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            for offset in range(days):
                day = end - timedelta(days=offset)
                s = NbaStatsResultsIngestor.ingest_day(conn, day, client=sdb)
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
