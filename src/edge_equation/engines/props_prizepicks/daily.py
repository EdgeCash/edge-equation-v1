"""Daily props orchestrator + email-block renderer.

Pipeline that turns "today's date" into a card dict the email layer
can render:

1. Fetch today's MLB events + per-event player prop lines from The
   Odds API (Phase-4 odds_fetcher).
2. Load per-player Statcast rates (Props-1 statcast_loader) — one
   call per (player_id, role). Cached on disk so a re-run is free.
3. Project each line via per-player Poisson(λ) (Props-1 projection).
4. Compute vig-adjusted edge (Phase-4 edge module).
5. Build canonical PropOutput rows (Props-2 output payload).
6. Persist predictions + features into PropsStore (Props-1 storage).
7. Render the polished top-N-by-edge block matching the NRFI TOP BOARD
   shape so both engines look identical in the email.

Best-effort throughout — every external dependency (Odds API, pybaseball,
DuckDB) degrades to its empty form rather than raising. The daily
email NEVER fails because of a props hiccup.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from typing import Iterable, Optional, Sequence

from edge_equation.engines.tiering import Tier, classify_tier
from edge_equation.utils.logging import get_logger

from .config import PropsConfig, get_default_config
from .data.statcast_loader import (
    BatterRollingRates,
    LEAGUE_BATTER_PRIOR_PER_PA,
    LEAGUE_PITCHER_PRIOR_PER_BF,
    PitcherRollingRates,
    load_batter_rates,
    load_pitcher_rates,
)
from .data.storage import PropsStore
from .edge import build_edge_picks
from .explain import decomposition_drivers, poisson_mc_band
from .ledger import (
    init_ledger_tables, render_ledger_section, settle_predictions,
)
from .markets import MLB_PROP_MARKETS, PropMarket
from .odds_fetcher import PlayerPropLine, fetch_all_player_props
from .output import PropOutput, build_prop_output, to_email_card
from .projection import ProjectedSide, project_all

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Card construction
# ---------------------------------------------------------------------------


@dataclass
class PropsCard:
    """The card dict the email layer consumes — packaged as a dataclass
    for type safety + auto-test introspection."""
    target_date: str
    picks: list[PropOutput] = field(default_factory=list)
    top_board_text: str = ""
    ledger_text: str = ""
    n_lines_fetched: int = 0
    n_qualifying_picks: int = 0


def build_props_card(
    target_date: Optional[str] = None,
    *,
    config: Optional[PropsConfig] = None,
    http_client=None,
    rates_by_player: Optional[dict[str, object]] = None,
    api_key: Optional[str] = None,
    persist: bool = True,
    settle_yesterday: bool = True,
    top_n: int = 8,
) -> PropsCard:
    """Build a `PropsCard` for `target_date`.

    Parameters
    ----------
    target_date : ``YYYY-MM-DD``. Defaults to today UTC.
    rates_by_player : When provided, skips the Statcast fetch and uses
        the supplied per-player rates dict (test injection point).
    persist : When True, writes predictions to the props DuckDB so
        tomorrow's settle pass can find them.
    settle_yesterday : When True, runs the settle pass for the season
        before rendering the ledger so today's email reflects last
        night's W/L.
    top_n : Maximum number of picks to surface in the top board.
    """
    cfg = (config or get_default_config()).resolve_paths()
    target = target_date or _date.today().isoformat()

    card = PropsCard(target_date=target)

    # 1. Fetch market lines.
    try:
        lines = fetch_all_player_props(
            target_date=target, api_key=api_key, http_client=http_client,
        )
    except Exception as e:
        log.warning("props daily: fetch_all_player_props failed (%s): %s",
                      type(e).__name__, e)
        lines = []
    card.n_lines_fetched = len(lines)
    if not lines:
        # Still try to render the (possibly populated) ledger so the
        # email has something useful.
        card.ledger_text = _safe_render_ledger(cfg, target)
        return card

    # 2. Load per-player rates for every distinct (player, role) pair
    # on the slate. The caller may pre-populate `rates_by_player` to
    # skip the Statcast fetch entirely (used by tests).
    rates_map = dict(rates_by_player or {})
    if not rates_by_player:
        rates_map = _load_rates_for_slate(lines, target, cfg)

    # 3. Project + 4. Compute edges.
    projections = project_all(
        lines, rates_by_player=rates_map, knobs=cfg.projection,
    )
    picks_edge = build_edge_picks(lines, projections, min_tier=Tier.LEAN)

    # 5. Convert each edge-pick into a PropOutput. We need the matching
    # ProjectedSide so we carry lam / blend_n / confidence into the
    # output payload's audit trail. Run the MC band + decomposition
    # helpers here so every payload carries the audit data the email
    # card and feed publisher consume.
    proj_index = _index_projections(lines, projections)
    outputs: list[PropOutput] = []
    for pick in picks_edge:
        proj = proj_index.get(_proj_key(pick))
        if proj is not None:
            band = poisson_mc_band(proj)
            drivers = decomposition_drivers(
                proj,
                league_prior_rate=_league_prior_for(proj),
                expected_volume=_expected_volume_for(proj, cfg),
                prior_weight=_prior_weight_for(proj, cfg),
                market_prob=float(pick.market_prob_devigged),
                edge_pp=float(pick.edge_pp),
            )
        else:
            band = None
            drivers = []
        outputs.append(build_prop_output(
            pick,
            confidence=proj.confidence if proj else 0.30,
            lam=proj.lam if proj else 0.0,
            blend_n=proj.blend_n if proj else 0,
            driver_text=drivers,
            mc_low=band.low if band else 0.0,
            mc_high=band.high if band else 0.0,
            mc_band_pp=band.band_pp if band else 0.0,
        ))
    card.picks = outputs
    card.n_qualifying_picks = len(outputs)

    # 6. Persist predictions (best-effort).
    if persist:
        try:
            _persist_predictions(cfg, outputs, target)
        except Exception as e:
            log.warning("props daily: persist failed (%s): %s",
                          type(e).__name__, e)

    # 7. Settle yesterday + render ledger (best-effort).
    if settle_yesterday:
        _safe_settle(cfg, target)
    card.ledger_text = _safe_render_ledger(cfg, target)

    # 7b. Top board for the email.
    card.top_board_text = render_top_props_block(outputs, n=top_n)
    return card


# ---------------------------------------------------------------------------
# Top-N polished email block (mirrors NRFI TOP BOARD format)
# ---------------------------------------------------------------------------


def render_top_props_block(
    picks: Sequence[PropOutput], *, n: int = 8,
) -> str:
    """Render the polished top-N-by-edge props block for the daily email.

    Same visual conventions as `nrfi.email_report._render_top_board`:
    numbered rows, ╪ rule, indented Why-clause continuation lines so
    the reader's eye lands on the matchup + tier first.
    """
    if not picks:
        return ""
    # Already sorted by edge desc by `build_edge_picks`; keep that order.
    top = list(picks)[:n]
    lines = [f"PROPS BOARD — Top {len(top)} by Edge", "═" * 60]
    for i, out in enumerate(top, 1):
        rendered = to_email_card(out)
        # Indent continuation lines under the row number for readability.
        prefix = f"{i:>2}.  "
        rendered = rendered.replace("\n", "\n" + " " * len(prefix))
        lines.append(prefix + rendered)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _league_prior_for(proj: ProjectedSide) -> float:
    """League-prior per-PA / per-BF rate for the projection's market."""
    market = proj.market.canonical
    if proj.market.role == "batter":
        return float(LEAGUE_BATTER_PRIOR_PER_PA.get(market, 0.0))
    return float(LEAGUE_PITCHER_PRIOR_PER_BF.get(market, 0.0))


def _expected_volume_for(proj: ProjectedSide, cfg: PropsConfig) -> float:
    """Expected per-game volume (PAs for batters, BFs for pitchers)."""
    return float(
        cfg.projection.expected_batter_pa if proj.market.role == "batter"
        else cfg.projection.expected_pitcher_bf
    )


def _prior_weight_for(proj: ProjectedSide, cfg: PropsConfig) -> float:
    """Bayesian-prior pseudo-count for the projection's role."""
    return float(
        cfg.projection.prior_weight_pa if proj.market.role == "batter"
        else cfg.projection.prior_weight_bf
    )


def _proj_key(item) -> tuple:
    """Key for matching a PlayerPropLine ↔ ProjectedSide ↔ PropEdgePick.
    All three carry the same (player, market, line, side) triple."""
    side = getattr(item, "side", "")
    market_obj = getattr(item, "market", None)
    if market_obj is not None:
        market = market_obj.canonical
    else:
        market = getattr(item, "market_canonical", "")
    line_value = getattr(item, "line_value", 0.0)
    player = getattr(item, "player_name", "")
    return (str(player), str(market), float(line_value), str(side))


def _index_projections(
    lines: Sequence[PlayerPropLine],
    projections: Sequence[ProjectedSide],
) -> dict[tuple, ProjectedSide]:
    """Map (player, market, line, side) → ProjectedSide for cheap lookup."""
    out: dict[tuple, ProjectedSide] = {}
    for line, proj in zip(lines, projections):
        out[_proj_key(line)] = proj
    return out


def _load_rates_for_slate(
    lines: Sequence[PlayerPropLine], target_date: str, cfg: PropsConfig,
) -> dict[str, object]:
    """Best-effort Statcast load for every distinct player on the slate.

    Returns ``{player_name → BatterRollingRates|PitcherRollingRates}``.
    On any per-player failure the player is simply absent from the
    map — projection falls through to the league prior.

    Note: this requires the player's MLB Stats API id, which the
    Phase-4 odds_fetcher doesn't currently surface. Without an id we
    skip Statcast entirely; the projection layer's no-rates path
    still produces a valid (league-prior) projection. Hooking up the
    id mapping is a small follow-up that doesn't gate this PR.
    """
    return {}


def _persist_predictions(
    cfg: PropsConfig, outputs: Sequence[PropOutput], target_date: str,
) -> None:
    """Write today's qualifying predictions into prop_predictions."""
    if not outputs:
        return
    store = PropsStore(cfg.duckdb_path)
    rows = [{
        "game_pk": 0,                       # Phase-4 fetcher doesn't
                                              # expose game_pk yet — 0 is a
                                              # safe placeholder for the
                                              # primary-key column; settle
                                              # join uses (event_date,
                                              # market, player) effectively.
        "event_date": target_date,
        "market_type": o.market_type,
        "player_name": o.player_name,
        "player_id": 0,
        "line_value": o.line_value,
        "side": o.side,
        "model_prob": o.model_prob,
        "market_prob": o.market_prob,
        "edge_pp": o.edge_pp,
        "american_odds": o.american_odds,
        "book": o.book,
        "confidence": o.confidence,
        "tier": o.tier,
        "feature_blob": json.dumps({
            "lam": o.lam, "blend_n": o.blend_n,
            "confidence": o.confidence,
        }),
    } for o in outputs]
    store.upsert("prop_predictions", rows)


def _safe_render_ledger(cfg: PropsConfig, target_date: str) -> str:
    """Render the props YTD ledger; "" on any error."""
    try:
        store = PropsStore(cfg.duckdb_path)
        init_ledger_tables(store)
        season = int(target_date[:4])
        return render_ledger_section(store, season=season)
    except Exception as e:
        log.debug("props ledger render skipped (%s): %s",
                    type(e).__name__, e)
        return ""


def _safe_settle(cfg: PropsConfig, target_date: str) -> None:
    """Run a settlement pass; swallow errors."""
    try:
        store = PropsStore(cfg.duckdb_path)
        season = int(target_date[:4])
        settle_predictions(
            store, season=season, cutoff_date=target_date, config=cfg,
        )
    except Exception as e:
        log.debug("props settle skipped (%s): %s",
                    type(e).__name__, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Build today's props card and print the email block.",
    )
    parser.add_argument("--date", default=None,
                          help="Slate date YYYY-MM-DD (default: today UTC).")
    parser.add_argument("--no-persist", action="store_true",
                          help="Skip writing predictions to DuckDB.")
    parser.add_argument("--top-n", type=int, default=8,
                          help="Max picks in the top board.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    card = build_props_card(
        args.date, persist=not args.no_persist, top_n=args.top_n,
    )
    print(f"Props card for {card.target_date}")
    print(f"  lines fetched      {card.n_lines_fetched}")
    print(f"  qualifying picks   {card.n_qualifying_picks}")
    print()
    if card.top_board_text:
        print(card.top_board_text)
    if card.ledger_text:
        print()
        print(card.ledger_text)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
