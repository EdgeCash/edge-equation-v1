"""Track-record JSON exporter — engine ledgers → website public data.

Walks every settled-picks ledger (NRFI, Props, Full-Game, Parlay),
filters to LEAN-and-above (NO_PLAY is excluded — those aren't picks),
normalizes into one flat schema, and writes three JSON files into
``website/public/data/track-record/``:

* ``ledger.json``   — every settled pick, newest first. Drives the
                      public ledger table.
* ``summary.json``  — running record per (engine × tier × season).
                      Drives the tier-summary cards at the top of
                      the page.
* ``by-day.json``   — per-day rollup of W/L/Push counts. Drives the
                      sparkline + recent-form widgets.

Pure read operation. Never mutates the DuckDB. Safe to run any time;
typically slot into the daily cron after settlement.

CLI
~~~

::

    python -m edge_equation.engines.website.build_track_record \\
        --duckdb-path data/nrfi_cache/nrfi.duckdb \\
        --out-dir website/public/data/track-record

Output is overwrite-on-write — no append, no merge. The DuckDB itself
is the source of truth; the JSON files are a static export of its
current state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# Tier ranking — lowest to highest. NO_PLAY is intentionally absent;
# those aren't picks, so they don't enter the ledger.
LEAN_AND_ABOVE: tuple[str, ...] = ("LEAN", "MODERATE", "STRONG", "ELITE")
TIER_RANK: dict[str, int] = {t: i for i, t in enumerate(LEAN_AND_ABOVE)}


@dataclass
class LedgerRow:
    """One settled pick, normalized across engines."""
    engine: str          # 'nrfi' | 'props' | 'full_game' | 'parlay'
    sport: str           # 'MLB' for now; future engines fill in NFL/NCAAF
    market_type: str     # 'NRFI' / 'YRFI' / 'Spread' / 'Total' / 'ML' / prop key
    pick_label: str      # human-readable: 'KC @ BAL · NRFI'
    season: int
    tier: str            # 'LEAN' / 'MODERATE' / 'STRONG' / 'ELITE'
    predicted_p: float   # 0..1
    american_odds: float
    actual_hit: Optional[bool]   # None when pending (game in progress)
    units_delta: float           # 0.0 for pending
    settled_at: str              # ISO 8601


@dataclass
class TierSummary:
    """Running record for one (engine, season, tier) cell."""
    engine: str
    season: int
    tier: str
    n_settled: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    units_won: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Wins / (wins + losses). Pushes excluded — they're refunds, not signal."""
        denom = self.wins + self.losses
        return float(self.wins) / denom if denom > 0 else 0.0


@dataclass
class DayRollup:
    """One day's W/L/P count across all engines."""
    date: str
    n_settled: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    units_won: float = 0.0


# ---------------------------------------------------------------------------
# Per-engine ledger queries
# ---------------------------------------------------------------------------


_NRFI_QUERY = """
SELECT
    'nrfi'                              AS engine,
    'MLB'                               AS sport,
    s.market_type                       AS market_type,
    g.away_team || ' @ ' || g.home_team AS pick_label,
    s.season                            AS season,
    s.tier                              AS tier,
    s.predicted_p                       AS predicted_p,
    s.american_odds                     AS american_odds,
    s.actual_hit                        AS actual_hit,
    s.units_delta                       AS units_delta,
    s.settled_at                        AS settled_at
FROM nrfi_pick_settled s
LEFT JOIN games g ON g.game_pk = s.game_pk
WHERE UPPER(s.tier) IN ('LEAN', 'MODERATE', 'STRONG', 'ELITE')
"""


_PROPS_QUERY = """
SELECT
    'props'                                                         AS engine,
    'MLB'                                                           AS sport,
    market_type                                                     AS market_type,
    COALESCE(player_name, '') || ' · ' || market_type || ' ' ||
        COALESCE(side, '') || ' ' ||
        CAST(COALESCE(line_value, 0.0) AS VARCHAR)                  AS pick_label,
    season                                                          AS season,
    tier                                                            AS tier,
    predicted_p                                                     AS predicted_p,
    american_odds                                                   AS american_odds,
    actual_hit                                                      AS actual_hit,
    units_delta                                                     AS units_delta,
    settled_at                                                      AS settled_at
FROM props_pick_settled
WHERE UPPER(tier) IN ('LEAN', 'MODERATE', 'STRONG', 'ELITE')
"""


_FULL_GAME_QUERY = """
SELECT
    'full_game'                                              AS engine,
    'MLB'                                                    AS sport,
    market_type                                              AS market_type,
    COALESCE(g.away_team, '') || ' @ ' || COALESCE(g.home_team, '') ||
        ' · ' || market_type || ' ' || COALESCE(side, '')    AS pick_label,
    season                                                   AS season,
    tier                                                     AS tier,
    predicted_p                                              AS predicted_p,
    american_odds                                            AS american_odds,
    actual_hit                                               AS actual_hit,
    units_delta                                              AS units_delta,
    settled_at                                               AS settled_at
FROM fullgame_pick_settled fgs
LEFT JOIN games g ON g.game_pk = fgs.game_pk
WHERE UPPER(fgs.tier) IN ('LEAN', 'MODERATE', 'STRONG', 'ELITE')
"""


_PARLAY_QUERY = """
SELECT
    'parlay'                  AS engine,
    'MLB'                     AS sport,
    'PARLAY'                  AS market_type,
    COALESCE(label, 'Parlay') AS pick_label,
    season                    AS season,
    tier                      AS tier,
    predicted_p               AS predicted_p,
    american_odds             AS american_odds,
    actual_hit                AS actual_hit,
    units_delta               AS units_delta,
    settled_at                AS settled_at
FROM parlay_ledger
WHERE UPPER(tier) IN ('LEAN', 'MODERATE', 'STRONG', 'ELITE')
"""


_QUERIES: dict[str, str] = {
    "nrfi":      _NRFI_QUERY,
    "props":     _PROPS_QUERY,
    "full_game": _FULL_GAME_QUERY,
    "parlay":    _PARLAY_QUERY,
}


def _table_exists(store, name: str) -> bool:
    """Defensive: a fresh DuckDB may not have every engine's tables yet."""
    try:
        store.query_df(f"SELECT 1 FROM {name} LIMIT 1")
        return True
    except Exception:
        return False


def _engine_table(engine: str) -> str:
    return {
        "nrfi":      "nrfi_pick_settled",
        "props":     "props_pick_settled",
        "full_game": "fullgame_pick_settled",
        "parlay":    "parlay_ledger",
    }[engine]


def _load_engine_rows(store, engine: str) -> list[LedgerRow]:
    if not _table_exists(store, _engine_table(engine)):
        return []
    sql = _QUERIES[engine]
    try:
        df = store.query_df(sql)
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    rows: list[LedgerRow] = []
    for _, r in df.iterrows():
        tier = str(r.get("tier") or "").upper()
        if tier not in TIER_RANK:
            continue
        rows.append(LedgerRow(
            engine=str(r.get("engine") or engine),
            sport=str(r.get("sport") or "MLB"),
            market_type=str(r.get("market_type") or ""),
            pick_label=str(r.get("pick_label") or "").strip() or "(unlabeled)",
            season=int(r.get("season") or 0),
            tier=tier,
            predicted_p=_safe_float(r.get("predicted_p")),
            american_odds=_safe_float(r.get("american_odds")),
            actual_hit=_safe_bool_or_none(r.get("actual_hit")),
            units_delta=_safe_float(r.get("units_delta")),
            settled_at=_iso(r.get("settled_at")),
        ))
    return rows


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_summary(rows: Iterable[LedgerRow]) -> list[TierSummary]:
    """Roll up per (engine, season, tier). Excludes pending rows so
    the 'hit rate' columns reflect only settled outcomes."""
    bucket: dict[tuple[str, int, str], TierSummary] = {}
    for r in rows:
        if r.actual_hit is None:
            continue
        key = (r.engine, r.season, r.tier)
        if key not in bucket:
            bucket[key] = TierSummary(engine=r.engine, season=r.season, tier=r.tier)
        s = bucket[key]
        s.n_settled += 1
        if r.units_delta > 0:
            s.wins += 1
        elif r.units_delta < 0:
            s.losses += 1
        else:
            s.pushes += 1
        s.units_won += float(r.units_delta or 0.0)
    return list(bucket.values())


def aggregate_by_day(rows: Iterable[LedgerRow]) -> list[DayRollup]:
    """One row per calendar date, summed across engines."""
    bucket: dict[str, DayRollup] = {}
    for r in rows:
        if r.actual_hit is None:
            continue
        date = (r.settled_at or "")[:10] or "unknown"
        if date not in bucket:
            bucket[date] = DayRollup(date=date)
        d = bucket[date]
        d.n_settled += 1
        if r.units_delta > 0:
            d.wins += 1
        elif r.units_delta < 0:
            d.losses += 1
        else:
            d.pushes += 1
        d.units_won += float(r.units_delta or 0.0)
    return sorted(bucket.values(), key=lambda x: x.date)


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


@dataclass
class TrackRecordBundle:
    """Everything the website needs in one struct. Each top-level field
    becomes its own JSON file via :func:`write_bundle`."""
    generated_at: str
    ledger: list[LedgerRow] = field(default_factory=list)
    summary: list[TierSummary] = field(default_factory=list)
    by_day: list[DayRollup] = field(default_factory=list)


def build_bundle(store) -> TrackRecordBundle:
    """Pull every engine's settled picks, aggregate, return one bundle."""
    rows: list[LedgerRow] = []
    for engine in _QUERIES.keys():
        rows.extend(_load_engine_rows(store, engine))
    # Newest-first for the public table.
    rows.sort(key=lambda r: (r.settled_at or "", r.engine), reverse=True)
    return TrackRecordBundle(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ledger=rows,
        summary=aggregate_summary(rows),
        by_day=aggregate_by_day(rows),
    )


def write_bundle(bundle: TrackRecordBundle, out_dir: str | Path) -> None:
    """Write the three JSON files. Overwrites any existing output."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "ledger.json").write_text(json.dumps({
        "version": 1,
        "generated_at": bundle.generated_at,
        "n_picks": len(bundle.ledger),
        "picks": [_row_to_dict(r) for r in bundle.ledger],
    }, indent=2) + "\n")

    (out / "summary.json").write_text(json.dumps({
        "version": 1,
        "generated_at": bundle.generated_at,
        "buckets": [_summary_to_dict(s) for s in bundle.summary],
    }, indent=2) + "\n")

    (out / "by-day.json").write_text(json.dumps({
        "version": 1,
        "generated_at": bundle.generated_at,
        "days": [_day_to_dict(d) for d in bundle.by_day],
    }, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _row_to_dict(r: LedgerRow) -> dict:
    return {
        "engine": r.engine,
        "sport": r.sport,
        "market_type": r.market_type,
        "pick_label": r.pick_label,
        "season": r.season,
        "tier": r.tier,
        "predicted_p": round(float(r.predicted_p), 4),
        "predicted_pct": round(float(r.predicted_p) * 100.0, 1),
        "american_odds": float(r.american_odds),
        "actual_hit": r.actual_hit,
        "units_delta": round(float(r.units_delta), 4),
        "settled_at": r.settled_at,
        "result": _result_label(r),
    }


def _summary_to_dict(s: TierSummary) -> dict:
    return {
        "engine": s.engine,
        "season": s.season,
        "tier": s.tier,
        "n_settled": s.n_settled,
        "wins": s.wins,
        "losses": s.losses,
        "pushes": s.pushes,
        "units_won": round(s.units_won, 3),
        "hit_rate": round(s.hit_rate, 4),
        "hit_pct": round(s.hit_rate * 100.0, 1),
    }


def _day_to_dict(d: DayRollup) -> dict:
    return {
        "date": d.date,
        "n_settled": d.n_settled,
        "wins": d.wins,
        "losses": d.losses,
        "pushes": d.pushes,
        "units_won": round(d.units_won, 3),
    }


def _result_label(r: LedgerRow) -> str:
    """W / L / Push / Pending — drives the badge color on the website."""
    if r.actual_hit is None:
        return "Pending"
    if r.units_delta > 0:
        return "W"
    if r.units_delta < 0:
        return "L"
    return "Push"


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _safe_float(v) -> float:
    try:
        if v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe_bool_or_none(v):
    if v is None:
        return None
    try:
        if isinstance(v, str) and v.strip() == "":
            return None
        return bool(v)
    except Exception:
        return None


def _iso(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return v.isoformat()
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the engine track record (LEAN-and-above) to JSON.",
    )
    parser.add_argument("--duckdb-path", required=True,
                          help="Path to the engine's DuckDB cache.")
    parser.add_argument("--out-dir", required=True,
                          help="Directory to write ledger.json / summary.json / by-day.json into.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from edge_equation.engines.nrfi.data.storage import NRFIStore
    store = NRFIStore(args.duckdb_path)
    try:
        bundle = build_bundle(store)
        write_bundle(bundle, args.out_dir)
    finally:
        store.close()

    if not args.quiet:
        print(
            f"Track-record JSON written to {args.out_dir}\n"
            f"  picks    {len(bundle.ledger)}\n"
            f"  buckets  {len(bundle.summary)}\n"
            f"  days     {len(bundle.by_day)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
