"""Units-only parlay ledger.

Per the audit thread: parlays are tracked by units returned, never by
W/L. Each ticket carries a unique `parlay_id`, the legs as JSON, the
fair / implied / model joint probabilities, the stake, and (after all
legs settle) the realized return.

Schema
------

::

    parlay_ledger
        parlay_id            VARCHAR PRIMARY KEY  (UUID4 hex by default)
        legs_json            VARCHAR              JSON list of leg dicts
        fair_decimal_odds    DOUBLE
        combined_decimal_odds DOUBLE
        implied_prob         DOUBLE
        model_joint_prob     DOUBLE
        stake_units          DOUBLE
        return_units         DOUBLE               NULL until settled
        recorded_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        settled_at           TIMESTAMP            NULL until settled
        notes                VARCHAR              free-form

There's deliberately **no** wins/losses column. The unit return is the
only metric that matters — chasing a "10 of 14 parlay record" with
flat-stake math is exactly the kind of accounting the audit warned
against. Units returned aggregates correctly across hits and misses.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from edge_equation.utils.logging import get_logger

from .builder import ParlayCandidate, ParlayLeg

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


_DDL_PARLAY_LEDGER = """
CREATE TABLE IF NOT EXISTS parlay_ledger (
    parlay_id              VARCHAR PRIMARY KEY,
    legs_json              VARCHAR,
    fair_decimal_odds      DOUBLE,
    combined_decimal_odds  DOUBLE,
    implied_prob           DOUBLE,
    model_joint_prob       DOUBLE,
    stake_units            DOUBLE,
    return_units           DOUBLE,
    recorded_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settled_at             TIMESTAMP,
    notes                  VARCHAR
)
"""


def init_parlay_tables(store) -> None:
    """Idempotent — call before any record/settle operation."""
    store.execute(_DDL_PARLAY_LEDGER)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leg_to_dict(leg: ParlayLeg) -> dict:
    """Wire-friendly serialisation of a leg for the legs_json column."""
    return {
        "market_type": leg.market_type,
        "side": leg.side,
        "side_probability": leg.side_probability,
        "american_odds": leg.american_odds,
        "tier": leg.tier.value,
        "game_id": leg.game_id,
        "player_id": leg.player_id,
        "label": leg.label,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    ).replace("+00:00", "")


# ---------------------------------------------------------------------------
# Record + settle
# ---------------------------------------------------------------------------


def record_parlay(
    store, candidate: ParlayCandidate, *,
    notes: Optional[str] = None,
    parlay_id: Optional[str] = None,
) -> str:
    """Persist a built `ParlayCandidate`. Returns the parlay_id.

    `return_units` is NULL until `settle_parlay` is called.
    """
    init_parlay_tables(store)
    pid = parlay_id or uuid.uuid4().hex
    legs_payload = [_leg_to_dict(l) for l in candidate.legs]
    store.upsert("parlay_ledger", [{
        "parlay_id": pid,
        "legs_json": json.dumps(legs_payload),
        "fair_decimal_odds": float(candidate.fair_decimal_odds)
            if candidate.fair_decimal_odds != float("inf") else None,
        "combined_decimal_odds": float(candidate.combined_decimal_odds),
        "implied_prob": float(candidate.implied_prob),
        "model_joint_prob": float(candidate.joint_prob_corr),
        "stake_units": float(candidate.stake_units),
        "return_units": None,
        "recorded_at": _now_iso(),
        "settled_at": None,
        "notes": notes,
    }])
    return pid


def settle_parlay(
    store, parlay_id: str, *,
    leg_outcomes: Sequence[bool],
) -> float:
    """Settle a recorded parlay against per-leg hit/miss outcomes.

    `leg_outcomes` is an array of booleans in the same order as the
    legs were recorded — True for a hit, False for a miss. The full
    ticket pays out `(combined_decimal_odds - 1) * stake` on all-hit
    and loses the stake on any-miss.

    Returns the realized `return_units` (signed, so a 0.5u stake on
    a +400 ticket that loses returns -0.5).
    """
    df = store.query_df(
        "SELECT combined_decimal_odds, stake_units, legs_json "
        "FROM parlay_ledger WHERE parlay_id = ?",
        (parlay_id,),
    )
    if df is None or df.empty:
        raise KeyError(f"parlay_id {parlay_id!r} not in ledger")
    row = df.iloc[0]
    combined_dec = float(row.combined_decimal_odds)
    stake = float(row.stake_units)
    legs = json.loads(row.legs_json)
    if len(leg_outcomes) != len(legs):
        raise ValueError(
            f"leg_outcomes length {len(leg_outcomes)} != "
            f"recorded leg count {len(legs)}"
        )

    all_hit = all(bool(o) for o in leg_outcomes)
    if all_hit:
        return_units = (combined_dec - 1.0) * stake
    else:
        return_units = -1.0 * stake

    store.execute(
        "UPDATE parlay_ledger "
        "SET return_units = ?, settled_at = ? "
        "WHERE parlay_id = ?",
        (float(return_units), _now_iso(), parlay_id),
    )
    return float(return_units)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_ledger(store):
    """Return all recorded parlays as a DataFrame, newest first.

    Empty DataFrame when nothing has been recorded yet.
    """
    return store.query_df(
        """
        SELECT parlay_id, legs_json, combined_decimal_odds,
               model_joint_prob, implied_prob,
               stake_units, return_units,
               recorded_at, settled_at, notes
        FROM parlay_ledger
        ORDER BY recorded_at DESC
        """,
    )


def render_ledger_section(store) -> str:
    """Plain-text YTD parlay block for the daily email / dashboard.

    Shows total ticket count, settled vs pending, and units returned
    in aggregate. Returns "" when nothing has been recorded.
    """
    df = get_ledger(store)
    if df is None or df.empty:
        return ""

    n_total = int(len(df))
    settled_mask = df["return_units"].notna()
    n_settled = int(settled_mask.sum())
    n_pending = n_total - n_settled
    units = float(df.loc[settled_mask, "return_units"].sum()) \
        if n_settled > 0 else 0.0
    total_stake = float(df.loc[settled_mask, "stake_units"].sum()) \
        if n_settled > 0 else 0.0
    roi_pct = (units / total_stake * 100.0) if total_stake > 0 else 0.0

    lines = [
        "PARLAY LEDGER",
        "─" * 60,
        f"  tickets recorded   {n_total}",
        f"  settled            {n_settled}",
        f"  pending            {n_pending}",
        f"  units returned     {units:+.2f}u",
    ]
    if total_stake > 0:
        lines.append(f"  ROI                {roi_pct:+.1f}%")
    return "\n".join(lines)
