"""Player profile data writers — shared scaffolding.

Writes the two JSON shapes the website's player profile page reads:

* ``<sport>/player_logs/<slug>.json``     — last-N stat-line table.
* ``<sport>/context_today/<slug>.json``   — today's live context.

The website schema is the source of truth — see
``web/lib/player-data.ts`` for the loader contract. Per-sport writer
modules in this package import from here for the slug rules and the
atomic writer so a copy edit lands once.

Engines never call into this directly. The master daily runner
(`run_daily_all.py`) orchestrates per-sport writers AFTER each sport's
unified card builds, so the writers see the freshly-projected slate
+ the populated DuckDBs the engines just wrote to.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# Repo-root anchored — `python run_daily_all.py` and the GitHub Actions
# master both run from the repo root, so this resolves correctly in
# both contexts.
_REPO_ROOT = Path(__file__).resolve().parents[4]
WEBSITE_DATA_ROOT = _REPO_ROOT / "website" / "public" / "data"


# ---------------------------------------------------------------------------
# Slug rule — must match `web/lib/search-index.ts:slugify`.
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase-alphanumeric slug with single-hyphen separators.

    Mirrors the website's slugify rule so the JSON file the writer
    drops at ``mlb/player_logs/aaron-judge.json`` is the same path
    the profile page resolves when a visitor lands at
    ``/player/mlb/aaron-judge``.
    """
    if not name:
        return ""
    s = _SLUG_RE.sub("-", name.lower())
    return s.strip("-")


# ---------------------------------------------------------------------------
# JSON shapes — kept in lockstep with web/lib/player-data.ts.
# ---------------------------------------------------------------------------


@dataclass
class GameLogRow:
    date: str                       # 'YYYY-MM-DD'
    opponent: str                   # tricode
    is_home: bool
    result: Optional[str]           # 'W' / 'L' / 'T' / None
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GameLog:
    player: str
    rows: List[GameLogRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "player": self.player,
            "rows": [asdict(r) for r in self.rows],
        }


@dataclass
class ContextItem:
    label: str
    value: str


@dataclass
class TodaysContext:
    player: str
    items: List[ContextItem] = field(default_factory=list)
    as_of: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "player": self.player,
            "as_of": self.as_of or _now_iso(),
            "items": [asdict(i) for i in self.items],
        }


# ---------------------------------------------------------------------------
# Atomic write helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _atomic_write(path: Path, content: str) -> None:
    """Atomic JSON write via tempfile + os.replace.

    Avoids the website briefly reading a half-written file when the
    daily run is still in progress.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp_", suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_player_log(
    sport: str, log: GameLog, *,
    out_root: Optional[Path] = None,
) -> Path:
    """Persist a player's game log JSON. Returns the written path."""
    base = out_root or WEBSITE_DATA_ROOT
    slug = slugify(log.player)
    if not slug:
        raise ValueError(
            f"refusing to write player log with empty slug: {log.player!r}",
        )
    out = base / sport / "player_logs" / f"{slug}.json"
    _atomic_write(out, json.dumps(log.to_dict(), indent=2) + "\n")
    return out


def write_today_context(
    sport: str, context: TodaysContext, *,
    out_root: Optional[Path] = None,
) -> Path:
    """Persist today's-context JSON. Returns the written path."""
    base = out_root or WEBSITE_DATA_ROOT
    slug = slugify(context.player)
    if not slug:
        raise ValueError(
            f"refusing to write context with empty slug: {context.player!r}",
        )
    out = base / sport / "context_today" / f"{slug}.json"
    _atomic_write(out, json.dumps(context.to_dict(), indent=2) + "\n")
    return out


# ---------------------------------------------------------------------------
# Per-sport directory conveniences
# ---------------------------------------------------------------------------


def output_dirs_for_sport(sport: str) -> List[str]:
    """Directories the master workflow should commit for one sport.

    Returned as POSIX-style strings (the workflow's `git add` consumes
    them) so the master script's `--list-output-paths` can include
    them alongside the existing per-sport dirs.
    """
    return [
        f"website/public/data/{sport}/player_logs/",
        f"website/public/data/{sport}/context_today/",
    ]


def known_player_dirs(sports: Iterable[str]) -> List[str]:
    out: list[str] = []
    for s in sports:
        out.extend(output_dirs_for_sport(s))
    return out


# ---------------------------------------------------------------------------
# Honest "Limited Data" stub helpers
#
# Used by sports whose data layer doesn't yet expose game logs. The
# website renders a "Limited Data" empty state when the player_logs
# file is missing OR when its `rows` array is empty — either way the
# UX is honest about what we do and don't know. Stubs are still
# useful because they let the writer pipeline declare which players
# we KNOW about (just not what their stats are), which keeps the
# search index + the profile-page header populated.
# ---------------------------------------------------------------------------


def empty_log(player: str) -> GameLog:
    return GameLog(player=player, rows=[])


def empty_context(player: str, *, note: str = "") -> TodaysContext:
    items: list[ContextItem] = []
    if note:
        items.append(ContextItem(label="Note", value=note))
    return TodaysContext(player=player, items=items)
