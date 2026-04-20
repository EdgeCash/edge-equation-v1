"""
Data-source helpers for the API.

Runtime: live mock sources keyed on the current date, deterministic per day.
Tests inject fixtures directly and do not touch these functions.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.nfl_source import NflSource
from edge_equation.ingestion.soccer_source import SoccerSource
from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.ingestion.schema import Slate, LEAGUE_TO_SPORT, VALID_LEAGUES
from edge_equation.engine.slate_runner import run_slate
from edge_equation.engine.pick_schema import Pick
from edge_equation.premium.mc_simulator import MonteCarloSimulator
from edge_equation.premium.premium_pick import PremiumPick


SPORT_ALIASES = {
    "mlb": "MLB", "kbo": "KBO", "npb": "NPB",
    "nba": "NBA", "ncaab": "NCAAB",
    "nhl": "NHL",
    "nfl": "NFL", "ncaaf": "NCAAF",
    "soc": "SOC", "soccer": "SOC",
}


def _resolve_sport(sport: str) -> str:
    """Resolve a user-provided sport identifier to a canonical league code."""
    if not sport:
        raise ValueError("sport is required")
    normalized = SPORT_ALIASES.get(sport.lower().strip())
    if normalized is None and sport.upper() in VALID_LEAGUES:
        normalized = sport.upper()
    if normalized is None:
        raise ValueError(f"Unknown sport: {sport!r}")
    return normalized


def _source_for_league(league: str):
    if league in ("MLB", "KBO", "NPB"):
        return MlbLikeSource(league)
    if league in ("NBA", "NCAAB"):
        return NbaSource()
    if league == "NHL":
        return NhlSource()
    if league in ("NFL", "NCAAF"):
        return NflSource()
    if league == "SOC":
        return SoccerSource()
    raise ValueError(f"No source wired for league: {league!r}")


def get_slate_for_league(league: str, run_datetime: Optional[datetime] = None) -> Slate:
    """Load a typed Slate for the given league at the given run datetime."""
    run_dt = run_datetime or datetime.now()
    source = _source_for_league(league)
    return normalize_slate(
        source.get_raw_games(run_dt),
        source.get_raw_markets(run_dt),
    )


def get_combined_slate_for_all_sports(run_datetime: Optional[datetime] = None) -> Slate:
    """Combined slate across the leagues the engine currently serves public cards on."""
    run_dt = run_datetime or datetime.now()
    games, markets = [], []
    for league in ("MLB", "NBA", "NHL"):
        source = _source_for_league(league)
        games.extend(source.get_raw_games(run_dt))
        markets.extend(source.get_raw_markets(run_dt))
    return normalize_slate(games, markets)


def picks_for_today(run_datetime: Optional[datetime] = None) -> List[Pick]:
    """Run the engine over today's combined slate and return picks."""
    run_dt = run_datetime or datetime.now()
    slate = get_combined_slate_for_all_sports(run_dt)
    all_picks: List[Pick] = []
    for sport_filter in ("MLB", "NBA", "NHL"):
        all_picks.extend(run_slate(slate, sport_filter, public_mode=False))
    return all_picks


def premium_picks_for_today(
    run_datetime: Optional[datetime] = None,
    seed: int = 42,
    iterations: int = 1000,
) -> List[PremiumPick]:
    """Wrap today's picks with MC-derived distributions."""
    picks = picks_for_today(run_datetime)
    sim = MonteCarloSimulator(seed=seed, iterations=iterations)
    premium: List[PremiumPick] = []
    for pick in picks:
        if pick.fair_prob is not None:
            # Binary / ML-like: simulate around the fair probability
            dist = sim.simulate_binary(pick.fair_prob)
            notes = f"MC binary simulation, seed={seed}, iterations={iterations}."
        elif pick.expected_value is not None:
            # Total or rate prop: use 15% of mean as placeholder stdev
            mean = pick.expected_value
            stdev = (mean * Decimal("0.15")).quantize(Decimal("0.01"))
            dist = sim.simulate_total(mean, stdev)
            notes = (
                f"MC total simulation, seed={seed}, iterations={iterations}, "
                f"placeholder stdev=15% of mean."
            )
        else:
            # Shouldn't normally happen; skip gracefully
            continue
        premium.append(PremiumPick(
            base_pick=pick,
            p10=dist["p10"], p50=dist["p50"],
            p90=dist["p90"], mean=dist["mean"],
            notes=notes,
        ))
    return premium


def pick_to_out_dict(pick: Pick) -> dict:
    """Flatten a Pick to a JSON-friendly dict for API responses."""
    return {
        "selection": pick.selection,
        "market_type": pick.market_type,
        "sport": pick.sport,
        "line_odds": pick.line.odds,
        "line_number": str(pick.line.number) if pick.line.number is not None else None,
        "fair_prob": str(pick.fair_prob) if pick.fair_prob is not None else None,
        "expected_value": str(pick.expected_value) if pick.expected_value is not None else None,
        "edge": str(pick.edge) if pick.edge is not None else None,
        "grade": pick.grade,
        "kelly": str(pick.kelly) if pick.kelly is not None else None,
        "realization": pick.realization,
        "game_id": pick.game_id,
        "event_time": pick.event_time,
    }


def premium_pick_to_out_dict(pp: PremiumPick) -> dict:
    base = pick_to_out_dict(pp.base_pick)
    base.update({
        "p10": str(pp.p10) if pp.p10 is not None else None,
        "p50": str(pp.p50) if pp.p50 is not None else None,
        "p90": str(pp.p90) if pp.p90 is not None else None,
        "mean": str(pp.mean) if pp.mean is not None else None,
        "notes": pp.notes,
    })
    return base


def slate_entries_for_sport(sport: str, run_datetime: Optional[datetime] = None) -> List[dict]:
    """Raw slate entries for a given sport path-param."""
    league = _resolve_sport(sport)
    slate = get_slate_for_league(league, run_datetime)
    # Group ML and Total markets per game into flat slate entries
    markets_by_game: dict = {}
    for m in slate.markets:
        markets_by_game.setdefault(m.game_id, []).append(m)
    entries = []
    for g in slate.games:
        ml_home = None
        ml_away = None
        total = None
        for m in markets_by_game.get(g.game_id, []):
            if m.market_type == "ML":
                if m.selection == g.home_team:
                    ml_home = m.odds
                elif m.selection == g.away_team:
                    ml_away = m.odds
                else:
                    # Default to home odds when selection ambiguous
                    if ml_home is None:
                        ml_home = m.odds
            elif m.market_type == "Total" and total is None and m.line is not None:
                total = str(m.line)
        entries.append({
            "game_id": g.game_id,
            "home_team": g.home_team,
            "away_team": g.away_team,
            "moneyline_home": ml_home,
            "moneyline_away": ml_away,
            "total": total,
            "event_time": g.start_time.isoformat(),
        })
    return entries
