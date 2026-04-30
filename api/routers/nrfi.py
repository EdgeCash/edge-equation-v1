"""Elite NRFI/YRFI API router.

Exposes today's NRFI board (post-engine, post-calibration) for the
website / dashboard / Discord card. The route is read-only and does
not require auth — same as `/picks/today`.

Endpoints
---------
* `GET /nrfi/today` — list of per-game NRFI/YRFI predictions for the
  current MLB slate. Returns an empty list when the optional `[nrfi]`
  extras aren't installed (route stays mounted to keep the schema
  stable).

* `GET /nrfi/board?date=YYYY-MM-DD` — same shape, for an arbitrary
  date if the engine has predictions stored in DuckDB.

* `GET /nrfi/dashboard?date=YYYY-MM-DD` — Phase 5 aggregator. One
  payload that the Next.js dashboard consumes:
    { date, board: [...], ytd_ledger: [...],
      parlay_candidates: [...], parlay_ledger: {...} }

Both routes are intentionally tolerant of missing data: an empty list
is preferable to a 500 when the daily ETL hasn't yet run for the day.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query


router = APIRouter(prefix="/nrfi", tags=["nrfi"])


@router.get("/today")
def get_nrfi_today() -> List[dict]:
    """Return today's NRFI/YRFI board (one row per market per game)."""
    return _board_for(_date.today().isoformat())


@router.get("/board")
def get_nrfi_board(
    date: str = Query(..., description="YYYY-MM-DD"),
) -> List[dict]:
    return _board_for(date)


@router.get("/dashboard")
def get_nrfi_dashboard(
    date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today UTC.",
    ),
) -> Dict[str, Any]:
    """One-shot aggregator for the dashboard page.

    Returns ``{date, board, ytd_ledger, parlay_candidates, parlay_ledger}``.
    Each section degrades to its empty form when the underlying data
    isn't available — never raises a 500 just because today's ETL
    hasn't run or the parlay subsystem hasn't been touched yet.
    """
    target_date = date or _date.today().isoformat()
    return {
        "date": target_date,
        "board": _board_with_tiers_for(target_date),
        "ytd_ledger": _ytd_ledger_for(target_date),
        "parlay_candidates": _parlay_candidates_for(target_date),
        "parlay_ledger": _parlay_ledger_summary(),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _board_for(target_date: str) -> List[dict]:
    """Pull stored predictions from DuckDB. Quietly returns [] when the
    NRFI subsystem is not installed (so the API shape stays stable)."""
    try:
        from edge_equation.engines.nrfi.config import get_default_config
        from edge_equation.engines.nrfi.data.storage import NRFIStore
    except ImportError:
        return []
    try:
        cfg = get_default_config()
        store = NRFIStore(cfg.duckdb_path)
        df = store.predictions_for_date(target_date)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    keep_cols = [c for c in [
        "game_pk", "home_team", "away_team", "first_pitch_ts",
        "nrfi_pct", "lambda_total", "color_band", "signal",
        "mc_low", "mc_high", "edge", "kelly_units", "shap_drivers",
    ] if c in df.columns]
    return df[keep_cols].to_dict(orient="records")


def _board_with_tiers_for(target_date: str) -> List[dict]:
    """Same as ``_board_for`` but each row carries its tier classification.

    The dashboard renders a colored badge per pick, so tier is more
    useful client-side than the raw nrfi_pct alone.
    """
    rows = _board_for(target_date)
    if not rows:
        return rows
    try:
        from edge_equation.engines.tiering import classify_tier
    except ImportError:
        return rows
    enriched: List[dict] = []
    for r in rows:
        nrfi_pct = r.get("nrfi_pct")
        if nrfi_pct is None:
            enriched.append(r)
            continue
        try:
            nrfi_p = float(nrfi_pct) / 100.0
            yrfi_p = 1.0 - nrfi_p
            nrfi_clf = classify_tier(market_type="NRFI",
                                       side_probability=nrfi_p)
            yrfi_clf = classify_tier(market_type="YRFI",
                                       side_probability=yrfi_p)
            r["nrfi_tier"] = nrfi_clf.tier.value
            r["yrfi_tier"] = yrfi_clf.tier.value
        except Exception:
            pass
        enriched.append(r)
    return enriched


def _ytd_ledger_for(target_date: str) -> List[dict]:
    """Phase 3 per-tier YTD ledger rows for the season of `target_date`."""
    try:
        from edge_equation.engines.nrfi.config import get_default_config
        from edge_equation.engines.nrfi.data.storage import NRFIStore
        from edge_equation.engines.nrfi.ledger import get_tier_ledger
    except ImportError:
        return []
    try:
        season = int(target_date[:4])
        cfg = get_default_config()
        store = NRFIStore(cfg.duckdb_path)
        df = get_tier_ledger(store, season)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _parlay_candidates_for(target_date: str) -> List[dict]:
    """Build today's parlay candidates from stored predictions.

    Mirrors the email block's behaviour: tier-classify every prediction,
    keep STRONG-and-above sides, run through the parlay builder, return
    the top 2 candidates as JSON-friendly dicts.
    """
    try:
        from edge_equation.engines.nrfi.config import get_default_config
        from edge_equation.engines.nrfi.data.storage import NRFIStore
        from edge_equation.engines.parlay import (
            ParlayConfig, ParlayLeg, build_parlay_candidates,
        )
        from edge_equation.engines.tiering import Tier, classify_tier
    except ImportError:
        return []
    try:
        cfg = get_default_config()
        store = NRFIStore(cfg.duckdb_path)
        df = store.predictions_for_date(target_date)
    except Exception:
        return []
    if df is None or df.empty:
        return []

    legs: List[ParlayLeg] = []
    for _, row in df.iterrows():
        nrfi_pct = row.get("nrfi_pct")
        if nrfi_pct is None:
            continue
        try:
            nrfi_p = float(nrfi_pct) / 100.0
        except (TypeError, ValueError):
            continue
        game_pk = str(row.get("game_pk") or "")
        away = row.get("away_team") or ""
        home = row.get("home_team") or ""
        label_prefix = f"{away} @ {home}" if away and home else game_pk

        for market, side_p, side_label in (
            ("NRFI", nrfi_p, "Under 0.5"),
            ("YRFI", 1.0 - nrfi_p, "Over 0.5"),
        ):
            clf = classify_tier(market_type=market,
                                  side_probability=side_p)
            if clf.tier not in (Tier.LOCK, Tier.STRONG):
                continue
            odds = -120.0 if market == "NRFI" else -105.0
            legs.append(ParlayLeg(
                market_type=market, side=side_label,
                side_probability=side_p, american_odds=odds,
                tier=clf.tier, game_id=game_pk,
                label=f"{label_prefix} {market}",
            ))

    candidates = build_parlay_candidates(legs, config=ParlayConfig())[:2]
    out: List[dict] = []
    for c in candidates:
        out.append({
            "n_legs": c.n_legs,
            "joint_prob_independent": c.joint_prob_independent,
            "joint_prob_corr": c.joint_prob_corr,
            "combined_decimal_odds": c.combined_decimal_odds,
            "combined_american_odds": c.combined_american_odds,
            "implied_prob": c.implied_prob,
            "edge_pp": c.edge_pp,
            "ev_units": c.ev_units,
            "stake_units": c.stake_units,
            "legs": [{
                "market_type": l.market_type,
                "side": l.side,
                "side_probability": l.side_probability,
                "american_odds": l.american_odds,
                "tier": l.tier.value,
                "label": l.label,
            } for l in c.legs],
        })
    return out


def _parlay_ledger_summary() -> Dict[str, Any]:
    """Aggregate counts + units for the parlay_ledger.

    Returns ``{recorded, settled, pending, units_returned, total_stake, roi_pct}``
    or all-zeros when the table is empty / not yet created.
    """
    empty = {
        "recorded": 0, "settled": 0, "pending": 0,
        "units_returned": 0.0, "total_stake": 0.0, "roi_pct": 0.0,
    }
    try:
        from edge_equation.engines.nrfi.config import get_default_config
        from edge_equation.engines.nrfi.data.storage import NRFIStore
        from edge_equation.engines.parlay import get_ledger
    except ImportError:
        return empty
    try:
        cfg = get_default_config()
        store = NRFIStore(cfg.duckdb_path)
        df = get_ledger(store)
    except Exception:
        return empty
    if df is None or df.empty:
        return empty
    settled_mask = df["return_units"].notna()
    n_settled = int(settled_mask.sum())
    total = int(len(df))
    units = float(df.loc[settled_mask, "return_units"].sum()) \
        if n_settled > 0 else 0.0
    stake = float(df.loc[settled_mask, "stake_units"].sum()) \
        if n_settled > 0 else 0.0
    roi = (units / stake * 100.0) if stake > 0 else 0.0
    return {
        "recorded": total,
        "settled": n_settled,
        "pending": total - n_settled,
        "units_returned": units,
        "total_stake": stake,
        "roi_pct": roi,
    }
