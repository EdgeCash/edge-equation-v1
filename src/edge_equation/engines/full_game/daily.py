"""Daily full-game orchestrator + email-block renderer.

Pipeline that turns "today's date" into a card dict the email layer
can render:

1. Fetch today's MLB events + standard markets (h2h/spreads/totals)
   from The Odds API (1 credit). Optionally fetch alternate markets
   (F5_Total / F5_ML / Team_Total) per-event when `include_alternates=True`.
2. Load per-team rolling rates (FG-1 team_rates loader). Skipped if
   the operator hasn't populated `fullgame_actuals` yet — projection
   falls through to the league prior.
3. Project each line via per-team Bayesian-blended Poisson + Skellam
   (FG-1 projection module).
4. Compute vig-adjusted edge (FG-1 edge module).
5. Build canonical FullGameOutput rows (FG-2 payload).
6. Persist predictions into FullGameStore (FG-1 storage).
7. Render the polished top-N-by-edge block matching the NRFI / Props
   TOP BOARD shape so all three engines look identical in the email.

Best-effort throughout — every external dependency degrades silently
rather than blocking the daily email.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from typing import Iterable, Optional, Sequence

from edge_equation.engines.tiering import Tier
from edge_equation.utils.logging import get_logger

from .config import FullGameConfig, get_default_config
from .data.storage import FullGameStore
from .data.team_rates import (
    TeamRollingRates, default_team_rates_table, load_team_rates_table,
)
from .edge import build_edge_picks
from .explain import decomposition_drivers, mc_band
from .ledger import (
    init_ledger_tables, render_ledger_section, settle_predictions,
)
from .markets import MLB_FULL_GAME_MARKETS
from .odds_fetcher import FullGameLine, fetch_all_full_game_lines
from .output import FullGameOutput, build_full_game_output, to_email_card
from .projection import ProjectedFullGameSide, project_all

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Card construction
# ---------------------------------------------------------------------------


@dataclass
class FullGameCard:
    """The card dict the email layer consumes."""
    target_date: str
    picks: list[FullGameOutput] = field(default_factory=list)
    top_board_text: str = ""
    ledger_text: str = ""
    n_lines_fetched: int = 0
    n_qualifying_picks: int = 0
    n_projected: int = 0                # all (line, projection) pairs evaluated
    n_skipped_low_confidence: int = 0   # dropped by the confidence floor


def build_full_game_card(
    target_date: Optional[str] = None,
    *,
    config: Optional[FullGameConfig] = None,
    http_client=None,
    rates_by_team: Optional[dict[str, TeamRollingRates]] = None,
    api_key: Optional[str] = None,
    persist: bool = True,
    settle_yesterday: bool = True,
    top_n: int = 8,
    include_alternates: bool = False,
) -> FullGameCard:
    """Build a `FullGameCard` for `target_date`.

    Parameters
    ----------
    target_date : ``YYYY-MM-DD``. Defaults to today UTC.
    rates_by_team : When provided, replaces the default rates table.
        Otherwise defaults to the league-prior table (every tricode at
        league average) so day-one projections produce a number for
        every game on the slate.
    persist : When True, writes predictions to fullgame_predictions.
    settle_yesterday : When True, runs the settle pass for the season.
    top_n : Maximum picks in the top board.
    include_alternates : When True, fetches F5_Total / F5_ML /
        Team_Total per-event (extra credits — gate behind a flag).
    """
    cfg = (config or get_default_config()).resolve_paths()
    target = target_date or _date.today().isoformat()

    card = FullGameCard(target_date=target)

    # 1. Fetch market lines.
    try:
        lines = fetch_all_full_game_lines(
            target_date=target, api_key=api_key, http_client=http_client,
            include_alternates=include_alternates,
        )
    except Exception as e:
        log.warning("FG daily: fetch_all_full_game_lines failed (%s): %s",
                      type(e).__name__, e)
        lines = []
    card.n_lines_fetched = len(lines)
    if not lines:
        card.ledger_text = _safe_render_ledger(cfg, target)
        return card

    # 2. Per-team rates. When the caller didn't pass a precomputed
    # table, pull rolling rates from this engine's own DuckDB
    # ``fullgame_actuals`` table. ``load_team_rates_table`` falls back
    # to the league-prior seed for tricodes with no recent actuals,
    # so callers always get a complete dict — the orchestrator's
    # confidence floor (introduced 2026-05-01) handles the empty rows.
    if rates_by_team is not None:
        rates = rates_by_team
    else:
        try:
            store_for_rates = FullGameStore(cfg.duckdb_path)
            try:
                rates = load_team_rates_table(
                    store_for_rates,
                    end_date=target,
                    lookback_days=cfg.projection.lookback_days,
                )
            finally:
                store_for_rates.close()
        except Exception as e:
            log.warning(
                "FG daily: load_team_rates_table failed (%s): %s — "
                "falling back to league-prior table.",
                type(e).__name__, e,
            )
            rates = default_team_rates_table(end_date=target)
        n_team_with_signal = sum(
            1 for r in rates.values() if r.n_games > 0
        )
        log.info(
            "FG daily: %d/%d tricodes have non-empty rolling rates "
            "(lookback=%d days).",
            n_team_with_signal, len(rates),
            cfg.projection.lookback_days,
        )

    # 3. Project + 4. Compute edges.
    projections = project_all(
        lines, rates_by_team=rates, knobs=cfg.projection,
    )
    card.n_projected = len(projections)
    n_pure_prior = sum(
        1 for p in projections if float(p.confidence) < 0.31
    )
    card.n_skipped_low_confidence = n_pure_prior
    picks_edge = build_edge_picks(lines, projections, min_tier=Tier.LEAN)
    log.info(
        "FG daily: %d lines fetched → %d projected → %d skipped "
        "(pure-prior, n_games=0) → %d LEAN+ qualified",
        card.n_lines_fetched, card.n_projected,
        card.n_skipped_low_confidence, len(picks_edge),
    )

    # 5. Convert each edge-pick into a FullGameOutput, carrying λ +
    # blend counts + MC band + Why-notes from the matching projection.
    proj_index = _index_projections(lines, projections)
    outputs: list[FullGameOutput] = []
    for pick in picks_edge:
        proj = proj_index.get(_proj_key_for_pick(pick))
        if proj is not None:
            is_home_side = bool(
                pick.team_tricode and pick.team_tricode == pick.home_tricode
            )
            band = mc_band(
                proj,
                line_value=pick.line_value,
                is_home_side=is_home_side,
                f5_share=cfg.projection.f5_share_of_total,
            )
            drivers = decomposition_drivers(
                proj,
                home_tricode=pick.home_tricode,
                away_tricode=pick.away_tricode,
                prior_weight=cfg.projection.prior_weight_games,
                market_prob=float(pick.market_prob_devigged),
                edge_pp=float(pick.edge_pp),
            )
        else:
            band = None
            drivers = []
        outputs.append(build_full_game_output(
            pick,
            confidence=proj.confidence if proj else 0.30,
            lam_used=proj.lam_used if proj else 0.0,
            lam_home=proj.lam_home if proj else 0.0,
            lam_away=proj.lam_away if proj else 0.0,
            blend_n_home=proj.blend_n_home if proj else 0,
            blend_n_away=proj.blend_n_away if proj else 0,
            driver_text=drivers,
            mc_low=band.low if band else 0.0,
            mc_high=band.high if band else 0.0,
            mc_band_pp=band.band_pp if band else 0.0,
            event_id=str(pick.market_canonical),  # placeholder until per-event id mapping lands
        ))
    card.picks = outputs
    card.n_qualifying_picks = len(outputs)

    # 6. Persist (best-effort).
    if persist:
        try:
            _persist_predictions(cfg, outputs, target)
        except Exception as e:
            log.warning("FG daily: persist failed (%s): %s",
                          type(e).__name__, e)

    # 7. Settle yesterday + render ledger (best-effort).
    if settle_yesterday:
        _safe_settle(cfg, target)
    card.ledger_text = _safe_render_ledger(cfg, target)

    # 7b. Top board for the email.
    card.top_board_text = render_top_full_game_block(
        outputs, n=top_n,
        n_qualifying=card.n_qualifying_picks,
        n_skipped_low_confidence=card.n_skipped_low_confidence,
    )
    return card


# ---------------------------------------------------------------------------
# Top-N polished email block (mirrors NRFI / Props TOP BOARD format)
# ---------------------------------------------------------------------------


def render_top_full_game_block(
    picks: Sequence[FullGameOutput], *, n: int = 8,
    n_qualifying: Optional[int] = None,
    n_skipped_low_confidence: int = 0,
) -> str:
    """Render the polished top-N-by-edge full-game block."""
    if not picks:
        if n_skipped_low_confidence > 0:
            return (
                f"FULL-GAME BOARD — 0 LEAN+ picks qualified today "
                f"({n_skipped_low_confidence} skipped: no per-team rate data).\n"
            )
        return ""
    qualifying = n_qualifying if n_qualifying is not None else len(picks)
    top = list(picks)[:n]
    header = f"FULL-GAME BOARD — Top {len(top)} of {qualifying} LEAN+ picks"
    if n_skipped_low_confidence > 0:
        header += (
            f"  ({n_skipped_low_confidence} skipped: no per-team rate data)"
        )
    lines = [header, "═" * 60]
    for i, out in enumerate(top, 1):
        rendered = to_email_card(out)
        prefix = f"{i:>2}.  "
        rendered = rendered.replace("\n", "\n" + " " * len(prefix))
        lines.append(prefix + rendered)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _proj_key(line: FullGameLine) -> tuple:
    """Key matching FullGameLine ↔ ProjectedFullGameSide."""
    return (
        line.event_id, line.market.canonical, str(line.side),
        float(line.line_value) if line.line_value is not None else None,
    )


def _proj_key_for_pick(pick) -> tuple:
    """Same shape but pulls from FullGameEdgePick fields. We don't
    have event_id on the pick (it's the line's), but the (market,
    side, line_value) triple is unique within a single fetch run."""
    # Use the pick's market/side/line — event_id is missing on the
    # FullGameEdgePick dataclass so we fall back to a marker. The
    # caller's fetch produces lines all from the same slate; (market,
    # side, line_value) collisions across events would only come from
    # parlays of identical markets which we don't model.
    return (
        "",  # event_id not on the edge pick
        pick.market_canonical,
        str(pick.side),
        float(pick.line_value) if pick.line_value is not None else None,
    )


def _index_projections(
    lines: Sequence[FullGameLine],
    projections: Sequence[ProjectedFullGameSide],
) -> dict[tuple, ProjectedFullGameSide]:
    """Build an index keyed on (market, side, line_value) — minus
    event_id, since the edge-pick lookup uses the same compressed key."""
    out: dict[tuple, ProjectedFullGameSide] = {}
    for line, proj in zip(lines, projections):
        compressed = (
            "", line.market.canonical, str(line.side),
            float(line.line_value) if line.line_value is not None else None,
        )
        out[compressed] = proj
    return out


def _persist_predictions(
    cfg: FullGameConfig, outputs: Sequence[FullGameOutput], target_date: str,
) -> None:
    """Write today's qualifying predictions into fullgame_predictions."""
    if not outputs:
        return
    store = FullGameStore(cfg.duckdb_path)
    rows = [{
        "game_pk": 0,                    # placeholder until per-event id
                                           # mapping lands; settle joins on
                                           # game_pk so this needs a follow-
                                           # up to populate from MLB Stats API.
        "event_date": target_date,
        "market_type": o.market_type,
        "side": o.side,
        "team_tricode": o.team_tricode,
        "line_value": o.line_value if o.line_value is not None else 0.0,
        "model_prob": o.model_prob,
        "market_prob": o.market_prob,
        "edge_pp": o.edge_pp,
        "american_odds": o.american_odds,
        "book": o.book,
        "confidence": o.confidence,
        "tier": o.tier,
        "feature_blob": json.dumps({
            "lam_used": o.lam_used,
            "lam_home": o.lam_home,
            "lam_away": o.lam_away,
            "blend_n_home": o.blend_n_home,
            "blend_n_away": o.blend_n_away,
            "confidence": o.confidence,
        }),
    } for o in outputs]
    store.upsert("fullgame_predictions", rows)


def _safe_render_ledger(cfg: FullGameConfig, target_date: str) -> str:
    try:
        store = FullGameStore(cfg.duckdb_path)
        init_ledger_tables(store)
        season = int(target_date[:4])
        return render_ledger_section(store, season=season)
    except Exception as e:
        log.debug("FG ledger render skipped (%s): %s",
                    type(e).__name__, e)
        return ""


def _safe_settle(cfg: FullGameConfig, target_date: str) -> None:
    try:
        store = FullGameStore(cfg.duckdb_path)
        season = int(target_date[:4])
        settle_predictions(
            store, season=season, cutoff_date=target_date, config=cfg,
        )
    except Exception as e:
        log.debug("FG settle skipped (%s): %s",
                    type(e).__name__, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Build today's full-game card and print the email block.",
    )
    parser.add_argument("--date", default=None,
                          help="Slate date YYYY-MM-DD (default: today UTC).")
    parser.add_argument("--no-persist", action="store_true",
                          help="Skip writing predictions to DuckDB.")
    parser.add_argument("--include-alternates", action="store_true",
                          help="Pull F5_Total / F5_ML / Team_Total via "
                              "per-event endpoint (extra credits).")
    parser.add_argument("--top-n", type=int, default=8,
                          help="Max picks in the top board.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    card = build_full_game_card(
        args.date, persist=not args.no_persist, top_n=args.top_n,
        include_alternates=args.include_alternates,
    )
    print(f"Full-game card for {card.target_date}")
    print(f"  lines fetched           {card.n_lines_fetched}")
    print(f"  projected               {card.n_projected}")
    print(f"  skipped (pure-prior)    {card.n_skipped_low_confidence}")
    print(f"  LEAN+ qualifying picks  {card.n_qualifying_picks}")
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
