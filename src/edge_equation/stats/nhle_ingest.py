"""
NHL API results ingestor.

Parallel to MlbStatsResultsIngestor. Targets api-web.nhle.com for NHL
game results, writes to GameResultsStore with an "NHL-STATS-<id>"
prefix so rows can't collide with TheSportsDB-sourced NHL rows.

Same output shape (GameResult), same idempotency (UNIQUE on game_id),
same audit summary (IngestSummary) -- drop-in compatible with the
backfill / auto-settle CLI plumbing.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Optional

from edge_equation.stats.nhle_client import NhleClient
from edge_equation.stats.results import GameResult, GameResultsStore


# NHL API gameState values:
#   FUT   - scheduled / future
#   PRE   - pre-game
#   LIVE  - in progress
#   CRIT  - critical (late game, OT, shootout)
#   OFF   - officially over (most common final state)
#   FINAL - final (alt final marker on some games)
# We accept anything that maps to a settled outcome.
_FINISHED_GAME_STATES = frozenset({"OFF", "FINAL"})


@dataclass(frozen=True)
class IngestSummary:
    """Per-run audit log -- mirrors MlbStats / TheSportsDB ingestors'
    shape so callers (CLI, workflows) can format output uniformly."""
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


def _team_name(block: Dict[str, Any]) -> str:
    """NHL API nests team name under name.default (or .fr for French).
    Fall back to abbrev if the name key is missing."""
    name_obj = block.get("name") or {}
    if isinstance(name_obj, dict):
        name = (name_obj.get("default") or "").strip()
        if name:
            return name
    abbrev = (block.get("abbrev") or "").strip()
    return abbrev


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


def _is_final(game: Dict[str, Any]) -> bool:
    state = (game.get("gameState") or "").strip()
    return state in _FINISHED_GAME_STATES


def parse_game_as_result(game: Dict[str, Any]) -> Optional[GameResult]:
    """Map an NHL API game dict -> GameResult, or None when the game
    isn't a settled-with-scores row.

    Defensive: missing fields / unparseable scores yield None so the
    ingestor counts skipped rows in skipped_no_scores / skipped_
    non_final rather than crashing the run.
    """
    if not isinstance(game, dict):
        return None
    home_block = game.get("homeTeam") or {}
    away_block = game.get("awayTeam") or {}
    home_team = _team_name(home_block)
    away_team = _team_name(away_block)
    if not home_team or not away_team:
        return None
    home_score = _parse_int(home_block.get("score"))
    away_score = _parse_int(away_block.get("score"))
    if home_score is None or away_score is None:
        return None
    if not _is_final(game):
        return None
    game_id_raw = game.get("id")
    if game_id_raw is None:
        return None
    game_id = f"NHL-STATS-{game_id_raw}"
    # NHL API prefers startTimeUTC (ISO string); gameDate is a date-only
    # fallback used when startTime is missing.
    start_time = str(
        game.get("startTimeUTC") or game.get("gameDate") or ""
    ).strip()
    return GameResult(
        result_id=None,
        game_id=game_id,
        league="NHL",
        home_team=home_team,
        away_team=away_team,
        start_time=start_time,
        home_score=home_score,
        away_score=away_score,
        status="final",
    )


class NhleResultsIngestor:
    """
    Pulls NHL game results from api-web.nhle.com for one day or a day
    range and writes finished-game results into GameResultsStore.

    - ingest_day(conn, day, client=None) -> IngestSummary
    - backfill(conn, days=30, end_day=None, client=None) -> IngestSummary
    """

    @staticmethod
    def ingest_day(
        conn: sqlite3.Connection,
        day: date,
        client: Optional[NhleClient] = None,
    ) -> IngestSummary:
        """Pull every NHL game for `day`, parse, write finalized ones
        to GameResultsStore. Returns an audit summary."""
        owns_client = client is None
        sdb = client or NhleClient()
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            games = sdb.score_for_date(conn, day=day)
            for game in games or []:
                events_seen += 1
                result = parse_game_as_result(game)
                if result is None:
                    home_score = (game.get("homeTeam") or {}).get("score")
                    away_score = (game.get("awayTeam") or {}).get("score")
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
            leagues_scanned=1,  # NHL only
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
        client: Optional[NhleClient] = None,
    ) -> IngestSummary:
        """Walk `days` days back from end_day (default: today) and call
        ingest_day for each. Aggregates the per-day summaries into a
        single IngestSummary so the CLI can print one audit line.
        """
        if days < 1:
            return IngestSummary(0, 0, 0, 0, 0, 0, 0)
        owns_client = client is None
        sdb = client or NhleClient()
        end = end_day or date.today()
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            for offset in range(days):
                day = end - timedelta(days=offset)
                s = NhleResultsIngestor.ingest_day(conn, day, client=sdb)
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
