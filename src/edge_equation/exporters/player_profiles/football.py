"""NFL + NCAAF player-profile writers — best-effort stubs.

Same shape as the WNBA writer: the football engines' context layer
(injuries, weather, lineups) hasn't been surfaced as a per-player
file artifact yet, so this writer emits "Limited data" stubs that
let the website's profile page render an honest empty state.

Both NFL and NCAAF use the same writer because their player-data
pipelines are identical at this stage; the per-sport label drives
the output directory.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

from .writer import (
    ContextItem,
    TodaysContext,
    empty_log,
    write_player_log,
    write_today_context,
)

log = get_logger(__name__)


def write_profiles_for_slate(
    *,
    sport: str,                       # 'nfl' | 'ncaaf'
    target_date: Optional[str] = None,
    player_names: Iterable[str],
    out_root: Optional[Path] = None,
) -> dict[str, int]:
    if sport not in ("nfl", "ncaaf"):
        raise ValueError(f"unsupported football sport: {sport!r}")
    target = target_date or _date.today().isoformat()
    counts = {"logs": 0, "context": 0}
    seen: set[str] = set()
    for name in player_names:
        name = (name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            write_player_log(sport, empty_log(name), out_root=out_root)
            counts["logs"] += 1
        except Exception as e:
            log.warning(
                "%s player-profile: write_player_log failed for %r: %s",
                sport.upper(), name, e,
            )
        ctx = TodaysContext(
            player=name,
            items=[
                ContextItem(label="Slate date", value=target),
                ContextItem(
                    label="Source",
                    value=(
                        f"Limited data — {sport.upper()} game logs + live "
                        f"context not yet wired into the engine pipeline."
                    ),
                ),
            ],
        )
        try:
            write_today_context(sport, ctx, out_root=out_root)
            counts["context"] += 1
        except Exception as e:
            log.warning(
                "%s player-profile: write_today_context failed for %r: %s",
                sport.upper(), name, e,
            )
    log.info(
        "%s player-profile: wrote %d log stubs + %d context files",
        sport.upper(), counts["logs"], counts["context"],
    )
    return counts
