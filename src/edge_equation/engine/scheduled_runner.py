"""
Scheduled runner.

Top-level orchestrator that ties the whole engine together for autonomous
runs (cron, Vercel scheduled function, GitHub Action, systemd timer -- any
scheduler that can call a Python entry point).

Flow per invocation:
  1. Resolve an ingestion source per league via SourceFactory.
  2. Pull raw games + markets; normalize into a Slate.
  3. Run the betting engine across the slate to produce Picks.
  4. Build a posting card via PostingFormatter.
  5. Persist the slate + every pick into SQLite (idempotent per slate_id).
  6. Publish to X, Discord, and Email (if publish=True; each independently
     falls through to its failsafe on failure).

Deterministic slate_id scheme: "{card_type}_{YYYYMMDD}" at a minimum,
suffixed with "_{sport}" when the run is single-sport. Re-running the same
slate_id is a no-op for picks and publishing -- no double inserts, no
double posts.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional
import sqlite3

from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.engine.slate_runner import run_slate
from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.ingestion.schema import LEAGUE_TO_SPORT, Slate
from edge_equation.ingestion.source_factory import SourceFactory
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.publishing.discord_publisher import DiscordPublisher
from edge_equation.publishing.email_publisher import EmailPublisher
from edge_equation.publishing.x_publisher import XPublisher


CARD_TYPE_DAILY = "daily_edge"
CARD_TYPE_EVENING = "evening_edge"
VALID_CARD_TYPES = (CARD_TYPE_DAILY, CARD_TYPE_EVENING)

DEFAULT_LEAGUES = ("MLB", "NFL", "NHL", "NBA", "KBO", "NPB")


@dataclass(frozen=True)
class RunSummary:
    """Deterministic record of one scheduled-runner invocation."""
    slate_id: str
    card_type: str
    run_datetime: str
    leagues: tuple
    n_games: int
    n_picks: int
    new_slate: bool
    publish_results: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "slate_id": self.slate_id,
            "card_type": self.card_type,
            "run_datetime": self.run_datetime,
            "leagues": list(self.leagues),
            "n_games": self.n_games,
            "n_picks": self.n_picks,
            "new_slate": self.new_slate,
            "publish_results": [r.to_dict() if hasattr(r, "to_dict") else r
                                for r in self.publish_results],
        }


def _slate_id_for(
    card_type: str,
    run_datetime: datetime,
    leagues: List[str],
) -> str:
    date_part = run_datetime.date().isoformat().replace("-", "")
    if len(leagues) == 1:
        return f"{card_type}_{date_part}_{leagues[0].lower()}"
    return f"{card_type}_{date_part}"


def _collect_slate(
    leagues: List[str],
    run_datetime: datetime,
    conn: sqlite3.Connection,
    csv_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    prefer_mock: bool = False,
) -> Slate:
    """Walk the leagues, resolve a source for each, and merge into one Slate."""
    all_games: list = []
    all_markets: list = []
    for league in leagues:
        source = SourceFactory.for_league(
            league=league,
            run_date=run_datetime.date(),
            conn=conn,
            csv_dir=csv_dir,
            api_key=api_key,
            prefer_mock=prefer_mock,
        )
        all_games.extend(source.get_raw_games(run_datetime))
        all_markets.extend(source.get_raw_markets(run_datetime))
    return normalize_slate(all_games, all_markets)


def _picks_from_slate(slate: Slate, leagues: List[str]) -> List[Pick]:
    """Run the engine per unique sport so SPORT_CONFIG lookups succeed."""
    sports = sorted({LEAGUE_TO_SPORT[l] for l in leagues if l in LEAGUE_TO_SPORT})
    picks: List[Pick] = []
    for sport in sports:
        picks.extend(run_slate(slate, sport=sport))
    return picks


def _build_card(
    card_type: str,
    picks: List[Pick],
    run_datetime: datetime,
) -> dict:
    return PostingFormatter.build_card(
        card_type=card_type,
        picks=picks,
        generated_at=run_datetime.isoformat(),
    )


def _publish_card(
    card: dict,
    dry_run: bool,
    publishers: Optional[List[object]] = None,
) -> list:
    """Fan out to every provided publisher (or the three default ones)."""
    if publishers is None:
        publishers = [XPublisher(), DiscordPublisher(), EmailPublisher()]
    return [p.publish_card(card, dry_run=dry_run) for p in publishers]


class ScheduledRunner:
    """
    One-shot orchestrator:
    - run(card_type, conn, run_datetime, leagues=None, publish=False,
          dry_run=True, csv_dir=None, api_key=None, publishers=None,
          prefer_mock=False) -> RunSummary
    - settle(conn) -> dict  (convenience wrapper over RealizationTracker)

    Idempotency: slate_id is deterministic from (card_type, date, leagues).
    If a slate with that id already exists, picks are NOT re-inserted and
    the publish step is skipped. Force re-run by deleting the slate first.
    """

    @staticmethod
    def run(
        card_type: str,
        conn: sqlite3.Connection,
        run_datetime: Optional[datetime] = None,
        leagues: Optional[List[str]] = None,
        publish: bool = False,
        dry_run: bool = True,
        csv_dir: Optional[str] = None,
        api_key: Optional[str] = None,
        publishers: Optional[List[object]] = None,
        prefer_mock: bool = False,
    ) -> RunSummary:
        if card_type not in VALID_CARD_TYPES:
            raise ValueError(
                f"card_type must be one of {VALID_CARD_TYPES}, got {card_type!r}"
            )
        run_dt = run_datetime or datetime.utcnow()
        leagues_list = list(leagues) if leagues is not None else list(DEFAULT_LEAGUES)
        if not leagues_list:
            raise ValueError("at least one league required")

        slate_id = _slate_id_for(card_type, run_dt, leagues_list)
        existing = SlateStore.get(conn, slate_id)
        if existing is not None:
            # Idempotent: return a summary of the already-persisted slate.
            picks = PickStore.list_by_slate(conn, slate_id)
            return RunSummary(
                slate_id=slate_id,
                card_type=card_type,
                run_datetime=run_dt.isoformat(),
                leagues=tuple(leagues_list),
                n_games=0,
                n_picks=len(picks),
                new_slate=False,
                publish_results=(),
            )

        slate = _collect_slate(
            leagues=leagues_list,
            run_datetime=run_dt,
            conn=conn,
            csv_dir=csv_dir,
            api_key=api_key,
            prefer_mock=prefer_mock,
        )
        picks = _picks_from_slate(slate, leagues_list)

        SlateStore.insert(conn, SlateRecord(
            slate_id=slate_id,
            generated_at=run_dt.isoformat(),
            sport=leagues_list[0] if len(leagues_list) == 1 else None,
            card_type=card_type,
            metadata={
                "leagues": leagues_list,
                "prefer_mock": prefer_mock,
                "csv_dir": csv_dir or "",
            },
        ))
        PickStore.insert_many(conn, picks, slate_id=slate_id, recorded_at=run_dt.isoformat())

        publish_results: tuple = ()
        if publish:
            card = _build_card(card_type, picks, run_dt)
            publish_results = tuple(_publish_card(card, dry_run=dry_run, publishers=publishers))

        return RunSummary(
            slate_id=slate_id,
            card_type=card_type,
            run_datetime=run_dt.isoformat(),
            leagues=tuple(leagues_list),
            n_games=len(slate.games),
            n_picks=len(picks),
            new_slate=True,
            publish_results=publish_results,
        )

    @staticmethod
    def settle(
        conn: sqlite3.Connection,
        slate_id: Optional[str] = None,
    ) -> dict:
        """
        Thin wrapper around RealizationTracker.settle_picks. Returns the
        tracker's dict summary plus hit_rate_by_grade for convenience.
        """
        from edge_equation.engine.realization import RealizationTracker
        settled = RealizationTracker.settle_picks(conn, slate_id=slate_id)
        return {
            "slate_id": slate_id,
            "matched": settled["matched"],
            "updated": settled["updated"],
            "hit_rate_by_grade": RealizationTracker.hit_rate_by_grade(conn),
        }
