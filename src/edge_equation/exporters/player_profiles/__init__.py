"""Player-profile data writers.

Top-level orchestrator that walks today's unified daily feed and
calls each per-sport writer for every player on the slate. The
master daily runner (`run_daily_all.py`) calls `build_for_slate()`
AFTER each sport's unified card builds, so the writers see today's
freshly-projected slate.

Per-sport writers live in their own modules (`mlb`, `wnba`,
`football`). Each emits two files per player:

  - ``website/public/data/<sport>/player_logs/<slug>.json``
  - ``website/public/data/<sport>/context_today/<slug>.json``

The website's `lib/player-data.ts` loader reads from those exact
paths. The slug rule in `writer.slugify` matches
`web/lib/search-index.ts` so the JSON file the writer drops at
``mlb/player_logs/aaron-judge.json`` is the same path the profile
page resolves when a visitor lands at ``/player/mlb/aaron-judge``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

from . import football, mlb, wnba
from .writer import (
    ContextItem,
    GameLog,
    GameLogRow,
    TodaysContext,
    output_dirs_for_sport,
    slugify,
    write_player_log,
    write_today_context,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Slate → player-name extractor
# ---------------------------------------------------------------------------


def players_from_slate_picks(picks: Iterable[dict]) -> list[str]:
    """Walk a list of pick dicts (the daily feed's `picks` array
    shape) and return every distinct player name.

    Player names live in the `selection` field for prop rows in the
    form `"<Player> · <Market> <Side> <Line>"`. Game-result rows
    don't carry a player name, so they're skipped.
    """
    names: list[str] = []
    seen: set[str] = set()
    for p in picks or []:
        sel = str(p.get("selection") or "")
        market = str(p.get("market_type") or "")
        # Player props always carry " · " between the player and the
        # market label.
        if " · " in sel and (
            market.startswith("PLAYER_PROP_")
            or market in {
                "Points", "Rebounds", "Assists", "PRA", "3PM",
                "Hits", "HR", "RBI", "K", "Total_Bases",
                "Pass_Yds", "Rush_Yds", "Rec_Yds",
            }
            or "PLAYER" in market.upper()
        ):
            player = sel.split("·", 1)[0].strip()
            if player and player not in seen:
                names.append(player)
                seen.add(player)
    return names


def _split_mlb_role(picks: Iterable[dict]) -> tuple[list[str], list[str]]:
    """Return ``(batters, pitchers)`` from the MLB picks list.

    Heuristic: any prop in {Hits, HR, RBI, Total_Bases, Runs, SB,
    Singles, Doubles, Triples} is a batter; K / Outs / Earned_Runs
    are pitchers; everything else defaults to batter so the writer
    still attempts a Statcast pull.
    """
    pitcher_markets = {"K", "Outs", "Earned_Runs"}
    batters: list[str] = []
    pitchers: list[str] = []
    for p in picks or []:
        sel = str(p.get("selection") or "")
        market = str(p.get("market_type") or "")
        if " · " not in sel:
            continue
        # Strip the PLAYER_PROP_ prefix if present.
        canonical = market.removeprefix("PLAYER_PROP_")
        player = sel.split("·", 1)[0].strip()
        if not player:
            continue
        if canonical in pitcher_markets:
            if player not in pitchers:
                pitchers.append(player)
        else:
            if player not in batters:
                batters.append(player)
    return batters, pitchers


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_for_slate(
    feed: dict,
    *,
    target_date: Optional[str] = None,
    out_root: Optional[Path] = None,
    sports: Optional[Iterable[str]] = None,
) -> dict[str, dict[str, int]]:
    """Walk a unified daily feed and write per-player JSON for each sport.

    Returns a per-sport ``{logs: N, context: M}`` dict so the master
    runner can log a one-line summary at the end of the daily build.

    Set ``sports`` to a subset (e.g. ``("mlb",)``) to skip writers
    you don't want to run today. ``out_root`` overrides the default
    `website/public/data/` so tests can write into a tmp dir.
    """
    chosen = (
        set(s.lower() for s in sports)
        if sports is not None
        else {"mlb", "wnba", "nfl", "ncaaf"}
    )
    summary: dict[str, dict[str, int]] = {}

    if "mlb" in chosen:
        mlb_picks = list(feed.get("picks") or [])
        batters, pitchers = _split_mlb_role(mlb_picks)
        s_b = mlb.write_profiles_for_slate(
            target_date=target_date,
            player_names=batters,
            role="batter",
            out_root=out_root,
        )
        s_p = mlb.write_profiles_for_slate(
            target_date=target_date,
            player_names=pitchers,
            role="pitcher",
            out_root=out_root,
        )
        summary["mlb"] = {
            "logs": s_b["logs"] + s_p["logs"],
            "context": s_b["context"] + s_p["context"],
        }

    if "wnba" in chosen:
        wnba_picks = list((feed.get("wnba") or {}).get("picks") or [])
        names = players_from_slate_picks(wnba_picks)
        summary["wnba"] = wnba.write_profiles_for_slate(
            target_date=target_date,
            player_names=names,
            out_root=out_root,
        )

    if "nfl" in chosen:
        nfl_picks = list((feed.get("nfl") or {}).get("picks") or [])
        names = players_from_slate_picks(nfl_picks)
        summary["nfl"] = football.write_profiles_for_slate(
            sport="nfl",
            target_date=target_date,
            player_names=names,
            out_root=out_root,
        )

    if "ncaaf" in chosen:
        ncaaf_picks = list((feed.get("ncaaf") or {}).get("picks") or [])
        names = players_from_slate_picks(ncaaf_picks)
        summary["ncaaf"] = football.write_profiles_for_slate(
            sport="ncaaf",
            target_date=target_date,
            player_names=names,
            out_root=out_root,
        )

    return summary


__all__ = [
    "build_for_slate",
    "players_from_slate_picks",
    "ContextItem",
    "GameLog",
    "GameLogRow",
    "TodaysContext",
    "slugify",
    "write_player_log",
    "write_today_context",
    "output_dirs_for_sport",
    "mlb",
    "wnba",
    "football",
]
