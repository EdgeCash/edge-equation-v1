"""Daily-feed exporter — today's NRFI picks → website/daily/latest.json.

Reads today's predictions row + game metadata out of the engine's
DuckDB and writes the daily-feed JSON in the schema documented at
``website/public/data/daily/README.md``. The website's
``daily-edge.tsx`` page consumes it directly via ``loadDailyView``.

Manual-trigger workflow (2026-05-01): the operator runs the daily
email workflow, which re-runs ``run_daily`` to produce predictions,
then this exporter, then commits the JSON to main. Vercel auto-
deploys and `/daily-edge` shows today's open picks.

Scope: NRFI only for v1 of public testing. Props / Full-Game /
Parlay engines haven't been sanity-validated yet (the calibration
audit only proved NRFI passes the gate). Once those engines clear
their own sanity checks, extend ``_load_engine_picks`` to include
their daily-prediction tables.

CLI
~~~

::

    python -m edge_equation.engines.website.build_daily_feed \\
        --duckdb-path data/nrfi_cache/nrfi.duckdb \\
        --date 2026-05-01 \\
        --out-path website/public/data/daily/latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Output schema (matches website/public/data/daily/README.md v1)
# ---------------------------------------------------------------------------


@dataclass
class FeedPick:
    id: str
    sport: str
    market_type: str
    selection: str
    line_odds: float
    line_number: Optional[str]
    fair_prob: str
    edge: str
    kelly: str
    grade: str
    tier: Optional[str]
    notes: str
    event_time: Optional[str]
    game_id: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sport": self.sport,
            "market_type": self.market_type,
            "selection": self.selection,
            "line": {"number": self.line_number, "odds": self.line_odds},
            "fair_prob": self.fair_prob,
            "edge": self.edge,
            "kelly": self.kelly,
            "grade": self.grade,
            "tier": self.tier,
            "notes": self.notes,
            "event_time": self.event_time,
            "game_id": self.game_id,
        }


@dataclass
class FeedBundle:
    date: str
    generated_at: str
    notes: str = ""
    picks: list[FeedPick] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "generated_at": self.generated_at,
            "date": self.date,
            "source": "run_daily.py",
            "notes": self.notes,
            "picks": [p.to_dict() for p in self.picks],
        }


# ---------------------------------------------------------------------------
# DuckDB → picks
# ---------------------------------------------------------------------------


# Today's NRFI picks. Pull the prediction row plus game metadata so
# we can build a human-readable selection label. ``predictions`` is
# overwritten by each ``run_daily`` invocation, so it always holds
# the most recent slate.
_TODAY_NRFI_QUERY = """
SELECT
    p.game_pk            AS game_pk,
    p.nrfi_prob          AS nrfi_prob,
    p.nrfi_pct           AS nrfi_pct,
    p.lambda_total       AS lambda_total,
    p.color_band         AS color_band,
    p.market_prob        AS market_prob,
    p.edge               AS edge,
    p.kelly_units        AS kelly_units,
    g.away_team          AS away_team,
    g.home_team          AS home_team,
    g.first_pitch_ts     AS first_pitch_ts,
    g.game_date          AS game_date
FROM predictions p
LEFT JOIN games g ON g.game_pk = p.game_pk
WHERE g.game_date = ?
ORDER BY g.first_pitch_ts NULLS LAST, p.nrfi_prob DESC
"""


def _table_exists(store, name: str) -> bool:
    try:
        store.query_df(f"SELECT 1 FROM {name} LIMIT 1")
        return True
    except Exception:
        return False


def _load_nrfi_picks(store, target_date: str) -> list[FeedPick]:
    """Pull today's NRFI predictions, normalize into FeedPick rows.

    Returns an empty list (not an error) when there are no rows for
    the target date — typical on off-days or before ``run_daily`` has
    been triggered for the slate.
    """
    if not (_table_exists(store, "predictions") and _table_exists(store, "games")):
        return []
    df = store.query_df(_TODAY_NRFI_QUERY, (target_date,))
    if df is None or len(df) == 0:
        return []

    picks: list[FeedPick] = []
    for _, r in df.iterrows():
        prob = float(r.get("nrfi_prob") or 0.0)
        away = str(r.get("away_team") or "")
        home = str(r.get("home_team") or "")
        side = "NRFI" if prob >= 0.5 else "YRFI"
        # Flip the probability for YRFI so `fair_prob` is always the
        # probability of the SELECTED side, matching the schema.
        side_prob = prob if side == "NRFI" else 1.0 - prob
        edge_val = _safe_float(r.get("edge"))
        kelly_val = _safe_float(r.get("kelly_units"))
        market_prob = _safe_float(r.get("market_prob"))
        american = _market_prob_to_american(market_prob) if market_prob else -110.0

        picks.append(FeedPick(
            id=f"{int(r.get('game_pk') or 0)}-{side}",
            sport="MLB",
            market_type=side,
            selection=f"{side} · {away} @ {home}".strip(" ·"),
            line_odds=american,
            line_number=None,
            fair_prob=f"{side_prob:.4f}",
            edge=f"{edge_val:.4f}",
            kelly=f"{kelly_val:.4f}",
            grade=_grade_from_probability(side_prob),
            tier=None,   # populated once tier metadata flows through predictions
            notes=_notes_from_row(r, side, side_prob),
            event_time=_iso(r.get("first_pitch_ts")),
            game_id=str(int(r.get("game_pk") or 0)),
        ))
    return picks


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_bundle(store, target_date: str) -> FeedBundle:
    """Aggregate today's picks across engines (NRFI for v1)."""
    picks = _load_nrfi_picks(store, target_date)
    notes = (
        "Public-testing release. Manual operator trigger. Lineups + "
        "weather + umpires confirmed at publish time."
        if picks else
        "No picks for this slate yet — run_daily may not have been "
        "triggered, or there were no qualifying games today."
    )
    return FeedBundle(
        date=target_date,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        notes=notes,
        picks=picks,
    )


def write_bundle(bundle: FeedBundle, out_path: str | Path) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle.to_dict(), indent=2) + "\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(v) -> float:
    try:
        if v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v if v.endswith("Z") or "+" in v[10:] else v
    try:
        return v.isoformat()
    except Exception:
        return None


def _market_prob_to_american(market_prob: float) -> float:
    """Convert a vig-corrected market probability to American odds.
    Returns the rounded American value the website renders."""
    if market_prob <= 0 or market_prob >= 1:
        return -110.0
    if market_prob >= 0.5:
        return round(-100.0 * market_prob / (1.0 - market_prob))
    return round(100.0 * (1.0 - market_prob) / market_prob)


def _grade_from_probability(p: float) -> str:
    """Map fair probability to a coarse A+ … F grade. Mirrors the
    public ConvictionBadge boundaries on the website."""
    if p >= 0.70:
        return "A+"
    if p >= 0.64:
        return "A"
    if p >= 0.58:
        return "B"
    if p >= 0.55:
        return "C"
    if p >= 0.50:
        return "D"
    return "F"


def _notes_from_row(row, side: str, side_prob: float) -> str:
    """Short human-readable summary for the picks table."""
    lam = _safe_float(row.get("lambda_total"))
    band = str(row.get("color_band") or "").strip()
    parts = [f"{side_prob*100:.1f}% {side} (λ={lam:.2f})"]
    if band:
        parts.append(f"band: {band}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export today's NRFI picks to website/public/data/daily/latest.json.",
    )
    parser.add_argument("--duckdb-path", required=True)
    parser.add_argument(
        "--date", default=None,
        help="Slate date YYYY-MM-DD. Default: today (UTC).",
    )
    parser.add_argument("--out-path", required=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    target_date = args.date or date.today().isoformat()

    from edge_equation.engines.nrfi.data.storage import NRFIStore
    store = NRFIStore(args.duckdb_path)
    try:
        bundle = build_bundle(store, target_date)
        write_bundle(bundle, args.out_path)
    finally:
        store.close()

    if not args.quiet:
        print(
            f"Daily feed written to {args.out_path}\n"
            f"  date     {bundle.date}\n"
            f"  picks    {len(bundle.picks)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
