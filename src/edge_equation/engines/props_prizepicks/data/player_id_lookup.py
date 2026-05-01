"""Player-name → MLBAM id lookup with persistent JSON cache.

The Phase-4 odds_fetcher returns prop lines carrying ``player_name``
only. The Statcast loader needs an ``MLBAM player_id`` (an integer)
to call ``pybaseball.statcast_batter`` / ``statcast_pitcher``. This
module bridges the two with a thin wrapper around
``pybaseball.playerid_lookup`` plus a JSON cache so we don't pound
the Chadwick register on every run.

Cache layout
~~~~~~~~~~~~

``<cache_dir>/player_ids.json`` is a single dict mapping a
normalized name key to the MLBAM id (int) or null (negative cache —
"we tried to look this player up and pybaseball couldn't find them",
prevents repeated fruitless API hits).

Normalization: lowercased, accents stripped, double spaces collapsed.
The Odds API and Chadwick disagree on accents (e.g. "Ramón Laureano"
vs "Ramon Laureano") so we strip them both before the lookup.

Network resilience
~~~~~~~~~~~~~~~~~~

* All errors fall through to ``None`` — the projection layer will use
  the league prior, and the edge module's confidence floor will skip
  the resulting pick. The daily orchestrator never raises.
* The negative cache prevents a typo in a player name from costing us
  a round-trip every single run.

Use
~~~

    >>> resolver = PlayerIdResolver(cache_path="data/props_cache/player_ids.json")
    >>> resolver.resolve("Aaron Judge")
    592450
    >>> resolver.resolve("Some Misspelled Name")
    None  # cached as None to avoid repeated lookups
    >>> resolver.save()  # persist cache to disk
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace.

    The Odds API tends to keep diacritics ("Ramón Laureano") while
    Chadwick's normalized name is plain ASCII. We strip both before
    keying the cache so "Ramón" and "Ramon" hash to the same slot.
    """
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(name))
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_name).strip().lower()


def _split_name(name: str) -> tuple[str, str]:
    """Return (last, first) for ``"Aaron Judge"`` → ``("judge", "aaron")``.

    Multi-word last names ("De La Cruz") get the entire trailing
    portion as the last name, single-word first name as the first.
    The pybaseball API takes ``last, first`` separately.
    """
    parts = _normalize_name(name).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    # Heuristic: first token is first name, rest is last name.
    first = parts[0]
    last = " ".join(parts[1:])
    return last, first


class PlayerIdResolver:
    """Resolve player names to MLBAM ids with a persistent JSON cache.

    Construction is cheap; it loads the cache file lazily on first
    ``resolve`` call. Call ``save()`` at the end of a batch to persist
    any new entries to disk.
    """

    def __init__(self, cache_path: str | Path):
        self._cache_path = Path(cache_path)
        self._cache: Optional[dict[str, Optional[int]]] = None
        self._dirty = False

    def _load(self) -> None:
        if self._cache is not None:
            return
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text())
            except Exception as e:
                log.warning(
                    "player-id cache at %s unreadable (%s); starting fresh.",
                    self._cache_path, e,
                )
                self._cache = {}
        else:
            self._cache = {}

    def resolve(self, player_name: str) -> Optional[int]:
        """Return MLBAM id for ``player_name`` or ``None`` if not found.

        Cache hits return immediately. On cache miss, calls
        ``pybaseball.playerid_lookup``; result (positive or negative)
        is written back to the in-memory cache. Call ``save()`` to
        persist.
        """
        self._load()
        assert self._cache is not None  # for type checker
        key = _normalize_name(player_name)
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]

        mlbam_id = self._lookup_via_pybaseball(player_name)
        # Always cache, even None — prevents repeated fruitless lookups.
        self._cache[key] = mlbam_id
        self._dirty = True
        return mlbam_id

    def _lookup_via_pybaseball(self, player_name: str) -> Optional[int]:
        """Single-name lookup via pybaseball, swallow all errors → None."""
        try:
            import pybaseball  # type: ignore
        except ImportError:
            log.debug("pybaseball not available; cannot resolve '%s'.",
                        player_name)
            return None
        last, first = _split_name(player_name)
        if not last:
            return None
        try:
            df = pybaseball.playerid_lookup(last, first)
        except Exception as e:
            log.warning(
                "pybaseball.playerid_lookup raised for '%s' (%s): %s",
                player_name, type(e).__name__, e,
            )
            return None
        if df is None or len(df) == 0:
            return None
        # Multiple matches (common name) — prefer the most-recently-active
        # player (max mlb_played_last when the column is present, else
        # the first row Chadwick returns).
        try:
            if "mlb_played_last" in df.columns:
                df = df.sort_values("mlb_played_last", ascending=False,
                                      na_position="last")
            row = df.iloc[0]
            mlbam = row.get("key_mlbam")
            if mlbam is None or (hasattr(mlbam, "__class__") and
                                    mlbam.__class__.__name__ == "NaTType"):
                return None
            return int(mlbam)
        except Exception as e:
            log.warning(
                "playerid_lookup returned unexpected shape for '%s': %s",
                player_name, e,
            )
            return None

    def save(self) -> None:
        """Persist the cache to disk if anything new was learned."""
        if self._cache is None or not self._dirty:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._cache_path.write_text(
                json.dumps(self._cache, indent=2, sort_keys=True) + "\n",
            )
            self._dirty = False
        except Exception as e:
            log.warning(
                "failed to write player-id cache to %s: %s",
                self._cache_path, e,
            )

    def stats(self) -> dict:
        """Diagnostic — counts of cached entries / negative cache size."""
        self._load()
        assert self._cache is not None
        n_total = len(self._cache)
        n_resolved = sum(1 for v in self._cache.values() if v is not None)
        return {
            "n_total_cached": n_total,
            "n_resolved": n_resolved,
            "n_negative": n_total - n_resolved,
        }
