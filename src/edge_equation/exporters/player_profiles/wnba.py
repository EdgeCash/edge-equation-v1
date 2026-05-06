"""WNBA player-profile writer — best-effort stub.

The WNBA engine doesn't yet expose per-player game logs through the
codebase. Until that data layer lands, this writer emits "Limited
data" stubs for every player on today's slate so the website's
profile page renders an honest empty state instead of a 404.

Stubs are still useful: they confirm the writer pipeline knows
about the player, which keeps the search index + profile-header
populated. The website's `lib/player-data.ts` loader returns the
empty `rows` array unchanged and the `GameLogTable` component
renders the `Limited data — game logs not yet available for this
sport` message we already shipped in Phase 4.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

from .writer import (
    ContextItem,
    TodaysContext,
    empty_context,
    empty_log,
    write_player_log,
    write_today_context,
)

log = get_logger(__name__)


def write_profiles_for_slate(
    *,
    target_date: Optional[str] = None,
    player_names: Iterable[str],
    out_root: Optional[Path] = None,
) -> dict[str, int]:
    """Write empty-but-present player_logs + a Limited-data context
    file for every WNBA player on today's slate."""
    target = target_date or _date.today().isoformat()
    counts = {"logs": 0, "context": 0}
    seen: set[str] = set()
    for name in player_names:
        name = (name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            write_player_log("wnba", empty_log(name), out_root=out_root)
            counts["logs"] += 1
        except Exception as e:
            log.warning(
                "WNBA player-profile: write_player_log failed for %r: %s",
                name, e,
            )
        ctx = TodaysContext(
            player=name,
            items=[
                ContextItem(label="Slate date", value=target),
                ContextItem(
                    label="Source",
                    value=(
                        "Limited data — WNBA game logs + live context "
                        "not yet wired into the engine pipeline."
                    ),
                ),
            ],
        )
        try:
            write_today_context("wnba", ctx, out_root=out_root)
            counts["context"] += 1
        except Exception as e:
            log.warning(
                "WNBA player-profile: write_today_context failed for %r: %s",
                name, e,
            )
    log.info(
        "WNBA player-profile: wrote %d log stubs + %d context files",
        counts["logs"], counts["context"],
    )
    return counts
