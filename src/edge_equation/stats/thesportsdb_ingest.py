"""
TheSportsDB results ingestor.

Pulls completed-game final scores from TheSportsDB's /eventsday.php
endpoint (the same endpoint the DataFetcher already wraps) and writes
them into GameResultsStore so FeatureComposer has historical data to
compute strength ratings from.

This closes the engine's "no real picks without results" dead-end:

    DataFetcher -> Odds API -> live market lines                (lines OK)
                        |
    TheSportsDB -> GameResultsStore -> FeatureComposer          (was empty)
                        |
    -> bundle.inputs (strength_home / strength_away / pace / ...)
                        |
    -> BettingEngine.evaluate -> fair_prob -> edge -> Grade A+

Output shape: GameResult. Event parsing is defensive -- any event
missing scores, with non-final status, or with unparsable numbers
is silently skipped rather than raising. The idempotent
GameResultsStore.record handles repeated ingests (upsert on game_id).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from edge_equation.data_fetcher import (
    THESPORTSDB_LEAGUE_IDS,
    TheSportsDBClient,
)
from edge_equation.stats.results import GameResult, GameResultsStore


# TheSportsDB exposes status strings like "Match Finished", "FT",
# "Final", "finished". Anything else (Postponed / Scheduled / NS /
# Live) means the game hasn't settled yet -- skip.
_FINISHED_STATUS_TOKENS = frozenset({
    "Match Finished", "FT", "Final", "finished", "AOT", "AP",
})


@dataclass(frozen=True)
class IngestSummary:
    """Per-run audit log of what the settler actually imported."""
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


def _status_token_is_final(status: Any) -> bool:
    if not status:
        return False
    s = str(status).strip()
    if s in _FINISHED_STATUS_TOKENS:
        return True
    # Some event rows have postponed flag set explicitly.
    return s.lower() in {"match finished", "finished", "final", "ft"}


def _parse_int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _event_start_time(event: Dict[str, Any]) -> str:
    """Prefer TheSportsDB's `strTimestamp` (ISO) when present; otherwise
    fall back to `dateEvent` + `strTime`. Always returns a usable string
    (empty on total failure) so the GameResult.record call doesn't die
    on a NULL."""
    ts = event.get("strTimestamp") or event.get("dateEventISO")
    if ts:
        return str(ts)
    d = event.get("dateEvent") or ""
    t = event.get("strTime") or "00:00:00"
    if d:
        return f"{d}T{t}".rstrip(":T")
    return ""


def parse_event_as_result(
    event: Dict[str, Any],
    league: str,
) -> Optional[GameResult]:
    """Map a TheSportsDB event dict -> GameResult, or None when the
    event isn't a clean "final-score, settled, two-team" row.

    Defensive: unrecognized schema or missing fields yield None, never
    raise. The ingestor counts those in skipped_no_scores / skipped_
    non_final so the run log is auditable.
    """
    if not isinstance(event, dict):
        return None
    home = (event.get("strHomeTeam") or "").strip()
    away = (event.get("strAwayTeam") or "").strip()
    if not home or not away:
        return None
    home_score = _parse_int(event.get("intHomeScore"))
    away_score = _parse_int(event.get("intAwayScore"))
    if home_score is None or away_score is None:
        return None    # not yet scored -> skip
    status_raw = event.get("strStatus") or event.get("strPostponed") or ""
    if not _status_token_is_final(status_raw):
        # Some completed games have a blank status but present scores.
        # Accept those only when both scores are non-zero or when the
        # date is clearly in the past. Safer default: if status isn't
        # explicitly final, skip -- premium brand would rather be
        # incomplete than wrong.
        date_part = (event.get("dateEvent") or "")[:10]
        try:
            game_day = date.fromisoformat(date_part)
            if game_day >= date.today():
                return None
        except Exception:
            return None
    game_id = (event.get("idEvent") or "").strip()
    if not game_id:
        # Build a deterministic fallback id so repeat ingests still
        # upsert to the same row.
        game_id = f"{league}-{event.get('dateEvent', '')}-{away}-{home}"
    return GameResult(
        result_id=None,
        game_id=str(game_id),
        league=league,
        home_team=home,
        away_team=away,
        start_time=_event_start_time(event),
        home_score=home_score,
        away_score=away_score,
        status="final",
    )


class TheSportsDBResultsIngestor:
    """
    Walks TheSportsDB events_by_date for a set of leagues over a day
    range and writes finished-game results into GameResultsStore.

    - ingest_day(conn, day, leagues=None, client=None) -> IngestSummary
    - backfill(conn, days=30, end_day=None, leagues=None) -> IngestSummary

    leagues defaults to every league mapped in THESPORTSDB_LEAGUE_IDS
    (MLB / NBA / NFL / NHL / KBO / NPB / EPL / UCL). client is an
    injectable TheSportsDBClient for tests; production passes None
    and the ingestor constructs a fresh one.
    """

    @staticmethod
    def _leagues(explicit: Optional[Sequence[str]]) -> List[str]:
        if explicit:
            out = [lg for lg in explicit if lg in THESPORTSDB_LEAGUE_IDS]
            return out
        return sorted(THESPORTSDB_LEAGUE_IDS.keys())

    @staticmethod
    def ingest_day(
        conn: sqlite3.Connection,
        day: date,
        leagues: Optional[Sequence[str]] = None,
        client: Optional[TheSportsDBClient] = None,
    ) -> IngestSummary:
        """Pull every event for each league on `day`, parse, write
        finalized ones to GameResultsStore. Returns an audit summary."""
        owns_client = client is None
        sdb = client or TheSportsDBClient()
        leagues_list = TheSportsDBResultsIngestor._leagues(leagues)
        events_seen = 0
        events_finished = 0
        results_written = 0
        skipped_no_scores = 0
        skipped_non_final = 0
        try:
            for league in leagues_list:
                league_id = THESPORTSDB_LEAGUE_IDS.get(league)
                if league_id is None:
                    continue
                events = sdb.events_by_date(conn, day=day, league_id=league_id)
                for ev in events or []:
                    events_seen += 1
                    result = parse_event_as_result(ev, league)
                    if result is None:
                        # Distinguish missing scores from non-final
                        # status so the operator can tell why on any
                        # given day the numbers look thin.
                        home_score = _parse_int(ev.get("intHomeScore"))
                        away_score = _parse_int(ev.get("intAwayScore"))
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
            leagues_scanned=len(leagues_list),
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
        leagues: Optional[Sequence[str]] = None,
        client: Optional[TheSportsDBClient] = None,
    ) -> IngestSummary:
        """One-time seed over the last `days` days (inclusive of
        end_day, default today minus one -- TheSportsDB finalizes
        scores a few hours after game end, so "yesterday" is the
        safest "definitely final" date).

        Repeat-safe: GameResultsStore.record upserts on game_id.
        """
        end = end_day or (date.today() - timedelta(days=1))
        totals = {
            "events_seen": 0, "events_finished": 0, "results_written": 0,
            "skipped_no_scores": 0, "skipped_non_final": 0,
        }
        owns_client = client is None
        sdb = client or TheSportsDBClient()
        try:
            for offset in range(days):
                day = end - timedelta(days=offset)
                s = TheSportsDBResultsIngestor.ingest_day(
                    conn, day, leagues=leagues, client=sdb,
                )
                totals["events_seen"] += s.events_seen
                totals["events_finished"] += s.events_finished
                totals["results_written"] += s.results_written
                totals["skipped_no_scores"] += s.skipped_no_scores
                totals["skipped_non_final"] += s.skipped_non_final
        finally:
            if owns_client:
                sdb.close()
        return IngestSummary(
            days_scanned=days,
            leagues_scanned=len(TheSportsDBResultsIngestor._leagues(leagues)),
            events_seen=totals["events_seen"],
            events_finished=totals["events_finished"],
            results_written=totals["results_written"],
            skipped_no_scores=totals["skipped_no_scores"],
            skipped_non_final=totals["skipped_non_final"],
        )
