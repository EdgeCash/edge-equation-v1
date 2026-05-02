"""Daily-feed exporter — today's picks → website/daily/latest.json.

Reads today's predictions row + game metadata out of each engine's
DuckDB and writes the daily-feed JSON in the schema documented at
``website/public/data/daily/README.md``. The website's
``daily-edge.tsx`` page consumes it directly via ``loadDailyView``.

Manual-trigger workflow (2026-05-01): the operator runs the daily
email workflow, which re-runs the engines to produce predictions,
then this exporter, then commits the JSON to main. Vercel auto-
deploys and `/daily-edge` shows today's open picks.

Engines included
~~~~~~~~~~~~~~~~

* **NRFI / YRFI** — has sanity-passed since 2026-04-30.
* **Player props** — gated by the Phase Props-4 sanity gate
  (``props_prizepicks.evaluation.sanity``). Picks with tier
  LEAN-and-above flow through; ``NO_PLAY`` is dropped here just like
  the props ledger does.
* **Full-Game** — gated by the Phase Full-Game-2 sanity gate
  (``full_game.evaluation.sanity``). Same LEAN+ filter applies.

Both the props and full-game joins are *optional*: if their
``--props-duckdb-path`` / ``--fullgame-duckdb-path`` aren't provided
(or the files don't exist), the exporter still produces a NRFI-only
feed and the website renders the same way as before.

CLI
~~~

::

    python -m edge_equation.engines.website.build_daily_feed \\
        --duckdb-path data/nrfi_cache/nrfi.duckdb \\
        --props-duckdb-path data/props_cache/props.duckdb \\
        --fullgame-duckdb-path data/fullgame_cache/fullgame.duckdb \\
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
        # ``_market_prob_to_american`` is internally NaN-safe, but
        # ``_safe_float`` already collapses NaN/None/garbage to 0.0,
        # which falls through to the ``-110.0`` default below.
        american = (
            _market_prob_to_american(market_prob)
            if market_prob > 0 else -110.0
        )

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
# Today's props picks
# ---------------------------------------------------------------------------


# Pull tier-LEAN-and-above predictions for the slate. We keep things
# defensive: an event_date row count of zero (or a missing table)
# returns []. The website renders fewer picks; nothing fails.
#
# The ``confidence > 0.30`` clause is the SQL belt-and-suspenders
# version of the orchestrator's confidence floor — picks with
# ``confidence == 0.30`` are pure-prior projections (no per-player
# Statcast data, every player projected as league-average). Those
# manufacture fake huge "edges" against the market. The orchestrator
# already excludes them but the SQL filter ensures historical rows
# from before the floor was added don't leak into the public feed.
_TODAY_PROPS_QUERY = """
SELECT
    game_pk,
    market_type,
    player_name,
    line_value,
    side,
    model_prob,
    market_prob,
    edge_pp,
    american_odds,
    book,
    confidence,
    tier,
    feature_blob,
    event_date
FROM prop_predictions
WHERE event_date = ?
  AND tier IN ('ELITE', 'STRONG', 'MODERATE', 'LEAN')
  AND confidence > 0.30
ORDER BY edge_pp DESC NULLS LAST
"""


def _market_label(market_type: str) -> str:
    """Map canonical market codes to the headline label the website renders."""
    mapping = {
        "HR": "Home Runs",
        "Hits": "Hits",
        "Total_Bases": "Total Bases",
        "RBI": "RBIs",
        "K": "Strikeouts",
    }
    return mapping.get(market_type, market_type.replace("_", " "))


def _grade_from_tier(tier: str) -> str:
    """Coarse map from the conviction tier to the public letter grade.

    The website tier-color logic relies on either ``grade`` or ``tier``,
    so we always populate ``tier`` directly and emit a consistent grade
    for legacy callers.
    """
    return {
        "ELITE":    "A+",
        "STRONG":   "A",
        "MODERATE": "B",
        "LEAN":     "C",
        "NO_PLAY":  "F",
    }.get((tier or "").upper(), "F")


def _load_props_picks(store, target_date: str) -> list[FeedPick]:
    """Pull today's tier-LEAN+ props predictions into FeedPick rows.

    Re-classifies every row using ``engines.tiering.classify_tier``
    with the CURRENT thresholds before publishing — the persisted
    ``tier`` column may reflect older thresholds from a previous run
    on the same slate, which would otherwise leak stale ELITE picks
    onto the website even after we tightened the ladder. The
    re-classification is cheap, idempotent, and keeps the feed
    self-healing against any persisted-with-stale-tier data.

    After re-classification the feed:
    * drops any pick whose updated tier is ``NO_PLAY``
      (was qualifying under old rules, isn't under new)
    * caps at ``PROPS_FEED_MAX_PICKS`` rows sorted by
      (tier rank, edge desc, conviction desc) so the website
      surfaces the operator's most actionable picks first.
    """
    from edge_equation.engines.tiering import (
        Tier, classify_tier,
    )

    if store is None:
        return []
    if not _table_exists(store, "prop_predictions"):
        return []
    df = store.query_df(_TODAY_PROPS_QUERY, (target_date,))
    if df is None or len(df) == 0:
        return []

    tier_rank = {Tier.ELITE: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
                   Tier.LEAN: 1, Tier.NO_PLAY: 0}

    candidates: list[tuple[int, float, float, FeedPick]] = []
    for _, r in df.iterrows():
        market_type = str(r.get("market_type") or "")
        player = str(r.get("player_name") or "")
        side = str(r.get("side") or "")
        line_value = _safe_float(r.get("line_value"))
        model_prob = _safe_float(r.get("model_prob"))
        edge_pp = _safe_float(r.get("edge_pp"))
        american = _safe_float(r.get("american_odds")) or -110.0
        confidence = _safe_float(r.get("confidence"))
        lam = _lam_from_blob(r.get("feature_blob"))
        edge_frac = edge_pp / 100.0

        # Re-classify with current thresholds.
        try:
            clf = classify_tier(
                market_type=market_type or "Hits",
                edge=edge_frac,
                side_probability=model_prob,
            )
            fresh_tier = clf.tier
        except Exception:
            fresh_tier = Tier.NO_PLAY
        if fresh_tier == Tier.NO_PLAY:
            continue
        tier_str = fresh_tier.value
        # We don't persist Kelly directly; the engine computes it on the
        # fly when it builds the email card. For the feed we provide a
        # conservative 1/4-Kelly-equivalent off the edge so the website's
        # bet-sizing helper stays consistent — the conviction tier is
        # the source of truth for stake sizing in the public ledger.
        kelly = max(0.0, edge_frac * 0.25)

        market_label = _market_label(market_type)
        selection = f"{player} · {market_label} {side} {line_value:g}"
        # Use the canonical "PLAYER_PROP_<MARKET>" label so the website
        # classifier (`pages/daily-edge.tsx::classify`) routes to Props.
        feed_market_type = f"PLAYER_PROP_{market_type.upper()}"
        notes = _props_notes(model_prob, side, lam, edge_pp, confidence)

        game_pk = int(r.get("game_pk") or 0)
        # game_pk is a placeholder (0) until the Phase-4 odds_fetcher
        # surfaces it; build a deterministic id from the prop tuple
        # instead so duplicates within a slate don't collide.
        pid = "-".join([
            str(game_pk),
            market_type,
            _slug(player),
            f"{line_value:g}",
            side.upper(),
        ])

        pick_obj = FeedPick(
            id=pid,
            sport="MLB",
            market_type=feed_market_type,
            selection=selection,
            line_odds=american,
            line_number=f"{line_value:g}",
            fair_prob=f"{model_prob:.4f}",
            edge=f"{edge_frac:.4f}",
            kelly=f"{kelly:.4f}",
            grade=_grade_from_tier(tier_str),
            tier=tier_str,
            notes=notes,
            event_time=None,           # commence_time not persisted yet
            game_id=str(game_pk),
        )
        # Sort key: tier rank desc, then edge desc, then prob desc
        candidates.append((
            -tier_rank.get(fresh_tier, 0),
            -edge_pp,
            -model_prob,
            pick_obj,
        ))

    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    capped = [t[3] for t in candidates[:PROPS_FEED_MAX_PICKS]]
    return capped


# Hard cap on the number of props rows the website renders. The
# props orchestrator routinely produces 200+ LEAN+ picks on a typical
# slate (lots of player-line combos beat the vig); shipping all of
# them turns the page into a wall of low-conviction noise. 30 is
# enough to surface every ELITE + STRONG pick on a typical day plus
# the top of the MODERATE band; LEAN-tier picks effectively become
# email-only at this cap (which matches their "content-only"
# semantics in the conviction key).
PROPS_FEED_MAX_PICKS: int = 30


def _slug(s: str) -> str:
    """Lowercase a-z0-9 only — used for stable pick ids."""
    return "".join(c if c.isalnum() else "-" for c in (s or "").lower()).strip("-")


def _lam_from_blob(blob) -> float:
    """Pull λ out of the persisted feature_blob (best-effort)."""
    if not blob:
        return 0.0
    try:
        return float(json.loads(blob).get("lam") or 0.0)
    except Exception:
        return 0.0


def _props_notes(
    model_prob: float, side: str, lam: float, edge_pp: float, confidence: float,
) -> str:
    parts = [f"{model_prob*100:.1f}% {side}"]
    if lam > 0:
        parts.append(f"λ={lam:.2f}")
    if edge_pp:
        sign = "+" if edge_pp >= 0 else ""
        parts.append(f"edge {sign}{edge_pp:.1f}pp")
    if confidence:
        parts.append(f"conf {int(round(confidence*100))}%")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Today's full-game picks
# ---------------------------------------------------------------------------


# Pull tier-LEAN-and-above predictions for the slate. Defensive: zero
# rows or a missing table returns []. ``confidence > 0.30`` filter
# matches the props-side rationale — see _TODAY_PROPS_QUERY comment.
_TODAY_FULLGAME_QUERY = """
SELECT
    game_pk,
    market_type,
    side,
    team_tricode,
    line_value,
    model_prob,
    market_prob,
    edge_pp,
    american_odds,
    book,
    confidence,
    tier,
    feature_blob,
    event_date
FROM fullgame_predictions
WHERE event_date = ?
  AND tier IN ('ELITE', 'STRONG', 'MODERATE', 'LEAN')
  AND confidence > 0.30
ORDER BY edge_pp DESC NULLS LAST
"""


# Map daily-feed market_type strings the website classifier groups
# under "Full Game" (see daily-edge.tsx::classify). All Full-Game
# canonical markets land in MONEYLINE / TOTAL / RUN_LINE / SPREAD or
# carry the *FULL_GAME* substring.
_FULLGAME_FEED_MARKET: dict[str, str] = {
    "ML":         "MONEYLINE",
    "F5_ML":      "MONEYLINE_FULL_GAME_F5",
    "Total":      "TOTAL",
    "F5_Total":   "TOTAL_FULL_GAME_F5",
    "Team_Total": "TEAM_TOTAL_FULL_GAME",
    "Run_Line":   "RUN_LINE",
}


def _fullgame_market_label(market_type: str) -> str:
    """Operator-facing market label."""
    return {
        "ML":         "Moneyline",
        "F5_ML":      "F5 Moneyline",
        "Total":      "Total Runs",
        "F5_Total":   "F5 Total Runs",
        "Team_Total": "Team Total Runs",
        "Run_Line":   "Run Line",
    }.get(market_type, market_type.replace("_", " "))


def _load_fullgame_picks(store, target_date: str) -> list[FeedPick]:
    """Pull today's tier-LEAN+ full-game predictions into FeedPick rows."""
    if store is None:
        return []
    if not _table_exists(store, "fullgame_predictions"):
        return []
    df = store.query_df(_TODAY_FULLGAME_QUERY, (target_date,))
    if df is None or len(df) == 0:
        return []

    # Same self-healing re-classification as the props side: trust
    # the model_prob + edge fields, ignore the persisted ``tier``
    # column (which may have been written under older thresholds).
    from edge_equation.engines.tiering import (
        Tier, classify_tier,
    )

    picks: list[FeedPick] = []
    for _, r in df.iterrows():
        market_type = str(r.get("market_type") or "")
        side = str(r.get("side") or "")
        team = str(r.get("team_tricode") or "")
        line_value = _safe_float(r.get("line_value"))
        model_prob = _safe_float(r.get("model_prob"))
        edge_pp = _safe_float(r.get("edge_pp"))
        american = _safe_float(r.get("american_odds")) or -110.0
        confidence = _safe_float(r.get("confidence"))
        lam_used = _fullgame_lam_from_blob(r.get("feature_blob"))
        edge_frac = edge_pp / 100.0

        try:
            clf = classify_tier(
                market_type=market_type or "ML",
                edge=edge_frac,
                side_probability=model_prob,
            )
            fresh_tier = clf.tier
        except Exception:
            fresh_tier = Tier.NO_PLAY
        if fresh_tier == Tier.NO_PLAY:
            continue
        tier_str = fresh_tier.value

        # Conservative 1/4-Kelly proxy off the edge — tier is the
        # source of truth for stake sizing in the public ledger.
        kelly = max(0.0, edge_frac * 0.25)

        market_label = _fullgame_market_label(market_type)
        feed_market_type = _FULLGAME_FEED_MARKET.get(
            market_type, f"FULL_GAME_{market_type.upper()}",
        )
        selection = _fullgame_selection(market_type, market_label, side,
                                          team, line_value)
        notes = _props_notes(model_prob, side, lam_used, edge_pp, confidence)

        game_pk = int(r.get("game_pk") or 0)
        pid = "-".join([
            str(game_pk),
            market_type,
            _slug(team or side),
            f"{line_value:g}",
        ])

        picks.append(FeedPick(
            id=pid,
            sport="MLB",
            market_type=feed_market_type,
            selection=selection,
            line_odds=american,
            line_number=None if market_type in ("ML", "F5_ML") else f"{line_value:g}",
            fair_prob=f"{model_prob:.4f}",
            edge=f"{edge_frac:.4f}",
            kelly=f"{kelly:.4f}",
            grade=_grade_from_tier(tier_str),
            tier=tier_str,
            notes=notes,
            event_time=None,
            game_id=str(game_pk),
        ))
    return picks


def _fullgame_lam_from_blob(blob) -> float:
    """Pull lam_used out of the persisted feature_blob (best-effort)."""
    if not blob:
        return 0.0
    try:
        d = json.loads(blob)
        return float(d.get("lam_used") or d.get("lam") or 0.0)
    except Exception:
        return 0.0


def _fullgame_selection(market_type: str, market_label: str, side: str,
                          team: str, line_value: float) -> str:
    """Human-readable selection label per market.

    Examples::

        NYY ML
        NYY -1.5
        Over 8.5  (Total)
        BOS Over 4.5 (Team_Total)
    """
    if market_type in ("ML", "F5_ML"):
        return f"{team or side} · {market_label}"
    if market_type == "Run_Line":
        sign = f"{line_value:+g}"
        return f"{team or side} · {market_label} {sign}"
    if market_type == "Team_Total":
        return f"{team} · {market_label} {side} {line_value:g}"
    # Total / F5_Total
    return f"{market_label} {side} {line_value:g}"


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_bundle(
    store, target_date: str,
    props_store=None,
    fullgame_store=None,
) -> FeedBundle:
    """Aggregate today's picks across engines.

    ``store`` is the NRFI DuckDB; ``props_store`` is the props DuckDB
    (optional); ``fullgame_store`` is the full-game DuckDB (optional).
    Missing engines are silently skipped — the bundle still renders.
    """
    picks = _load_nrfi_picks(store, target_date)
    picks.extend(_load_props_picks(props_store, target_date))
    picks.extend(_load_fullgame_picks(fullgame_store, target_date))
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
    """Coerce a DuckDB cell value to a float — NaN, None, and non-
    numeric strings all collapse to ``0.0`` so callers don't have to
    sprinkle ``isnan`` guards. Pandas NaN is the most common case here:
    ``run_daily.py`` doesn't populate ``market_prob`` on the Poisson
    baseline path, so DuckDB stores NaN; ``_market_prob_to_american``
    would otherwise raise ``ValueError: cannot convert float NaN to
    integer`` on the round() call.
    """
    try:
        if v is None:
            return 0.0
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    # NaN check — math.isnan rejects non-floats, but we already coerced.
    if f != f:   # NaN is the only float that doesn't equal itself
        return 0.0
    return f


def _market_prob_to_american(market_prob: float) -> float:
    """Convert a vig-corrected market probability to American odds.
    Returns the rounded American value the website renders. Treats
    pathological / NaN inputs as ``-110.0`` so the publish step
    never crashes on unexpected data."""
    if market_prob is None:
        return -110.0
    try:
        mp = float(market_prob)
    except (TypeError, ValueError):
        return -110.0
    if mp != mp:   # NaN
        return -110.0
    if mp <= 0 or mp >= 1:
        return -110.0
    if mp >= 0.5:
        return round(-100.0 * mp / (1.0 - mp))
    return round(100.0 * (1.0 - mp) / mp)


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v if v.endswith("Z") or "+" in v[10:] else v
    try:
        return v.isoformat()
    except Exception:
        return None


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
        description="Export today's picks to website/public/data/daily/latest.json.",
    )
    parser.add_argument("--duckdb-path", required=True,
                          help="NRFI DuckDB path.")
    parser.add_argument("--props-duckdb-path", default=None,
                          help="Props DuckDB path (optional). When omitted "
                               "or missing, props are excluded from the feed.")
    parser.add_argument("--fullgame-duckdb-path", default=None,
                          help="Full-Game DuckDB path (optional). When "
                               "omitted or missing, full-game picks are "
                               "excluded from the feed.")
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
    props_store = None
    if args.props_duckdb_path and Path(args.props_duckdb_path).exists():
        from edge_equation.engines.props_prizepicks.data.storage import PropsStore
        props_store = PropsStore(args.props_duckdb_path)
    fullgame_store = None
    if args.fullgame_duckdb_path and Path(args.fullgame_duckdb_path).exists():
        from edge_equation.engines.full_game.data.storage import FullGameStore
        fullgame_store = FullGameStore(args.fullgame_duckdb_path)
    try:
        bundle = build_bundle(
            store, target_date,
            props_store=props_store,
            fullgame_store=fullgame_store,
        )
        write_bundle(bundle, args.out_path)
    finally:
        store.close()
        if props_store is not None:
            props_store.close()
        if fullgame_store is not None:
            fullgame_store.close()

    if not args.quiet:
        print(
            f"Daily feed written to {args.out_path}\n"
            f"  date     {bundle.date}\n"
            f"  picks    {len(bundle.picks)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
