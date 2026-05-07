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
from pathlib import Path
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
    n_projected: int = 0                # all (line, projection) pairs evaluated
    n_skipped_low_confidence: int = 0   # dropped by the confidence floor


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
    passed_markets: Optional[set[str]] = None,
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
    passed_markets : When provided, drops every line whose
        `market.canonical` is NOT in this set BEFORE projection.
        This is the BRAND_GUIDE rolling-backtest gate -- pass the
        set returned by `gates.market_gate(<backtest_summary>)` and
        the daily orchestrator only ships markets that historically
        clear +1% ROI / Brier <0.246 over 200+ bets. None means no
        gate (all markets allowed; cold-start behaviour).
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

    # 1b. Apply the BRAND_GUIDE rolling-backtest market gate. Drops
    # entire markets that don't pass the +1% ROI / Brier <0.246 / 200+
    # bets test, before we spend Statcast lookups on them. None == cold
    # start (all markets allowed).
    if passed_markets is not None:
        n_before = len(lines)
        lines = [
            ln for ln in lines if ln.market.canonical in passed_markets
        ]
        n_dropped = n_before - len(lines)
        if n_dropped:
            log.info(
                "props daily: market gate dropped %d/%d lines "
                "(allowed: %s)",
                n_dropped, n_before, sorted(passed_markets),
            )
        if not lines:
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
    card.n_projected = len(projections)
    # Track how many were dropped by the confidence floor — they would
    # have been pure-prior projections (every player projected as
    # league average), which manufactures fake huge "edges" against the
    # market. Operators see this in the log as a transparency check.
    n_pure_prior = sum(
        1 for p in projections if float(p.confidence) < 0.31
    )
    card.n_skipped_low_confidence = n_pure_prior
    picks_edge = build_edge_picks(lines, projections, min_tier=Tier.LEAN)
    log.info(
        "props daily: %d lines fetched → %d projected → %d skipped "
        "(pure-prior, blend_n=0) → %d LEAN+ qualified",
        card.n_lines_fetched, card.n_projected,
        card.n_skipped_low_confidence, len(picks_edge),
    )

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
    card.top_board_text = render_top_props_block(
        outputs, n=top_n,
        n_qualifying=card.n_qualifying_picks,
        n_skipped_low_confidence=card.n_skipped_low_confidence,
    )
    return card


# ---------------------------------------------------------------------------
# Top-N polished email block (mirrors NRFI TOP BOARD format)
# ---------------------------------------------------------------------------


def render_top_props_block(
    picks: Sequence[PropOutput], *, n: int = 8,
    n_qualifying: Optional[int] = None,
    n_skipped_low_confidence: int = 0,
) -> str:
    """Render the polished top-N-by-edge props block for the daily email.

    Same visual conventions as `nrfi.email_report._render_top_board`:
    numbered rows, ═ rule, indented Why-clause continuation lines so
    the reader's eye lands on the matchup + tier first.

    The header shows ``Top {N} of {M} LEAN+ Props`` so the reader
    knows the published list isn't the whole slate. When we dropped
    pure-prior projections via the confidence floor, append the count
    so the audit trail is visible.
    """
    if not picks:
        if n_skipped_low_confidence > 0:
            return (
                f"PROPS BOARD — 0 LEAN+ Props qualified today "
                f"({n_skipped_low_confidence} skipped: no per-player rate data).\n"
            )
        return ""
    qualifying = n_qualifying if n_qualifying is not None else len(picks)
    top = list(picks)[:n]
    header = f"PROPS BOARD — Top {len(top)} of {qualifying} LEAN+ Props"
    if n_skipped_low_confidence > 0:
        header += (
            f"  ({n_skipped_low_confidence} skipped: no per-player rate data)"
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
    On any per-player failure (no MLBAM id, Statcast fetch fails,
    pybaseball not installed) the player is simply absent from the
    map — projection falls through to the league prior, and the
    edge module's confidence floor skips the resulting pick.

    Two-step pipeline:

    1. Resolve every distinct ``(player_name, role)`` on the slate to
       an MLBAM id via ``PlayerIdResolver`` (Chadwick register +
       persistent JSON cache so repeated runs don't re-pound the
       lookup endpoint).
    2. For each resolved player, fetch + aggregate their rolling
       rates via ``load_batter_rates`` / ``load_pitcher_rates`` (both
       cached as parquet, so day-2 runs reuse day-1 fetches for
       any window that didn't shift).

    Edge cases:

    * Two-way players (e.g. Ohtani) appear as both batter and pitcher
       lines on the same slate. The returned dict is keyed on
       ``player_name`` only, so we'd otherwise overwrite. We pick
       whichever role has more lines on the slate and log a warning.
       The minority role's lines fall through to the league prior.
       Acceptable until we re-key on ``(player_name, role)``.
    """
    from .data.player_id_lookup import PlayerIdResolver

    resolver = PlayerIdResolver(
        cache_path=Path(cfg.cache_dir) / "player_ids.json",
    )
    # Group lines by (player_name, role) so we make one Statcast call
    # per unique player+role pair, not one per line.
    role_counts: dict[tuple[str, str], int] = {}
    for line in lines:
        key = (line.player_name, line.market.role)
        role_counts[key] = role_counts.get(key, 0) + 1

    # Pick the dominant role per player so two-way players don't
    # silently overwrite each other in the returned dict.
    chosen_role: dict[str, str] = {}
    for (name, role), n in role_counts.items():
        prev = chosen_role.get(name)
        if prev is None:
            chosen_role[name] = role
        elif prev != role:
            prev_n = role_counts[(name, prev)]
            if n > prev_n:
                log.warning(
                    "props daily: %s appears as both '%s' (%d lines) "
                    "and '%s' (%d lines); using '%s' rates only.",
                    name, prev, prev_n, role, n, role,
                )
                chosen_role[name] = role
            else:
                log.warning(
                    "props daily: %s appears as both '%s' (%d lines) "
                    "and '%s' (%d lines); using '%s' rates only.",
                    name, role, n, prev, prev_n, prev,
                )

    rates_map: dict[str, object] = {}
    n_resolved = 0
    n_unresolved = 0
    for player_name, role in chosen_role.items():
        try:
            mlbam_id = resolver.resolve(player_name)
        except Exception as e:
            log.warning("props daily: resolve(%s) raised (%s): %s",
                          player_name, type(e).__name__, e)
            mlbam_id = None
        if mlbam_id is None:
            n_unresolved += 1
            continue
        n_resolved += 1
        try:
            if role == "batter":
                rates_map[player_name] = load_batter_rates(
                    int(mlbam_id),
                    player_name=player_name,
                    end_date=target_date,
                    days=cfg.projection.lookback_days,
                    config=cfg,
                )
            else:
                rates_map[player_name] = load_pitcher_rates(
                    int(mlbam_id),
                    player_name=player_name,
                    end_date=target_date,
                    days=cfg.projection.lookback_days,
                    config=cfg,
                )
        except Exception as e:
            log.warning(
                "props daily: load %s rates failed for %s (id=%s): %s: %s",
                role, player_name, mlbam_id, type(e).__name__, e,
            )

    # Persist any new name→id learnings so tomorrow's run skips them.
    try:
        resolver.save()
    except Exception as e:
        log.debug("props daily: resolver.save() skipped (%s): %s",
                    type(e).__name__, e)

    log.info(
        "props daily: resolved %d/%d players to MLBAM ids → %d "
        "Statcast rate frames loaded",
        n_resolved, len(chosen_role), len(rates_map),
    )
    return rates_map


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
        # ISO 8601 from the Odds API event payload. Threaded into
        # FeedPick.event_time downstream so the upcoming-only failsafe
        # has a real first-pitch timestamp to gate on.
        "commence_time": str(getattr(o, "commence_time", "") or ""),
    } for o in outputs]
    # Clear any rows from a prior orchestrator run for this same
    # date before inserting. ``upsert`` only replaces matching primary
    # keys; rows produced under older thresholds that no longer
    # qualify under the current ladder would otherwise persist and
    # leak onto the website. Delete-then-insert makes today's table
    # state a function of today's run alone.
    try:
        store.execute(
            "DELETE FROM prop_predictions WHERE event_date = ?",
            (target_date,),
        )
    except Exception as e:
        log.debug(
            "props persist: pre-insert delete skipped (%s): %s",
            type(e).__name__, e,
        )
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
