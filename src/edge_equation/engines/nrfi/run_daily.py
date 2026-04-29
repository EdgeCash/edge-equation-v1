"""Daily inference entry point.

Usage:

    python -m edge_equation.engines.nrfi.run_daily
    python -m edge_equation.engines.nrfi.run_daily 2026-04-27
    python -m edge_equation.engines.nrfi.run_daily --no-mc

Legacy ``python -m nrfi.run_daily`` invocations are supported by deployment
wrappers that point at this package path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from edge_equation.engines.core.posting.conviction import (
    conviction_band,
    electric_indices,
    format_conviction_line,
    render_conviction_key,
)

from .config import get_default_config
from .data.odds import capture_closing_lines, init_odds_tables, lookup_closing_odds
from .data.scrapers_etl import daily_etl
from .data.storage import NRFIStore
from .evaluation.backtest import reconstruct_features_for_date
from .ledger import render_ledger_section
from .models.inference import NRFIInferenceEngine
from .models.model_training import MODEL_VERSION, TrainedBundle
from .output import build_output
from edge_equation.utils.logging import get_logger

log = get_logger(__name__, "INFO")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NRFI/YRFI daily inference")
    parser.add_argument("date", nargs="?",
                        default=date.today().isoformat(),
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-mc", action="store_true",
                        help="Disable Monte Carlo refinement")
    parser.add_argument("--no-shap", action="store_true",
                        help="Disable SHAP explanations")
    parser.add_argument("--no-live-odds", action="store_true",
                        help="Skip today's best-effort Odds API pulls")
    parser.add_argument("--output-json", default="nrfi_output.json",
                        help="Where to drop the per-game JSON")
    args = parser.parse_args(argv)

    cfg = get_default_config()
    if args.no_mc:
        cfg = replace(cfg, enable_monte_carlo=False)
    if args.no_shap:
        cfg = replace(cfg, enable_shap=False)
    cfg = cfg.resolve_paths()

    store = NRFIStore(cfg.duckdb_path)
    init_odds_tables(store)

    # Pull schedule + lineups + ump.
    n = daily_etl(args.date, store, config=cfg)
    log.info("Hydrated %d games for %s", n, args.date)

    odds_status = LiveOddsStatus()
    if not args.no_live_odds:
        odds_status = _pull_live_odds(store, args.date, config=cfg)

    # Build features for every game on the slate.
    feats_per_game = reconstruct_features_for_date(args.date, store=store, config=cfg)
    if not feats_per_game:
        log.warning("No games found for %s", args.date)
        return 0

    # Try to load a trained bundle; fall back to deterministic Poisson.
    bundle: TrainedBundle | None = None
    try:
        bundle = TrainedBundle.load(cfg.model_dir, MODEL_VERSION)
        log.info("Loaded model bundle %s", MODEL_VERSION)
    except Exception:
        log.warning("No trained bundle found at %s — using Poisson baseline",
                    cfg.model_dir)

    if bundle is not None:
        engine = NRFIInferenceEngine(bundle, cfg)
        feature_dicts = [f for _, f in feats_per_game]
        game_pks = [pk for pk, _ in feats_per_game]
        american_odds = [
            lookup_closing_odds(store, int(pk), "NRFI")
            for pk in game_pks
        ]
        yrfi_american_odds = [
            lookup_closing_odds(store, int(pk), "YRFI")
            for pk in game_pks
        ]
        market_probs = [
            _implied_prob(odds) if odds is not None else None
            for odds in american_odds
        ]
        yrfi_market_probs = [
            _implied_prob(odds) if odds is not None else None
            for odds in yrfi_american_odds
        ]
        preds = engine.predict_many(
            feature_dicts,
            game_pks=game_pks,
            market_probs=market_probs,
            american_odds=[
                float(odds) if odds is not None else cfg.betting.default_juice
                for odds in american_odds
            ],
        )
        engine.attach_monte_carlo(preds, feature_dicts)
        rows = [p.as_row() for p in preds]
        _annotate_conviction(rows)
        side_rows = _side_rows_from_predictions(
            preds,
            yrfi_market_probs=yrfi_market_probs,
            yrfi_american_odds=yrfi_american_odds,
            config=cfg,
        )
    else:
        # Deterministic fallback — Poisson baseline only.
        rows = []
        side_rows = []
        for pk, f in feats_per_game:
            p = float(f.get("poisson_p_nrfi", 0.55))
            out = build_output(
                game_id=str(pk),
                blended_p=p,
                lambda_total=float(f.get("lambda_total", 1.0)),
                market_american_odds=lookup_closing_odds(store, int(pk), "NRFI"),
                engine="poisson_baseline",
                model_version="poisson_baseline_only",
            )
            rows.append({
                "game_pk": pk,
                "nrfi_prob": out.nrfi_prob,
                "nrfi_pct": out.nrfi_pct,
                "lambda_total": out.lambda_total,
                "color_band": out.color_band,
                "color_hex": out.color_hex,
                "signal": out.signal,
                "mc_low": out.mc_low,
                "mc_high": out.mc_high,
                "mc_band_pp": out.mc_band_pp,
                "shap_drivers": json.dumps(out.shap_drivers),
                "driver_text": json.dumps(out.driver_text),
                "market_prob": out.market_prob,
                "edge": out.edge,
                "edge_pp": out.edge_pp,
                "kelly_units": out.kelly_units,
                "kelly_suggestion": out.kelly_suggestion,
                "tier": out.tier,
                "tier_basis": out.tier_basis,
                "tier_value": out.tier_value,
                "tier_band": out.tier_band,
                "probability_display": out.headline(),
                "model_version": out.model_version,
            })
            side_rows.extend(_baseline_side_rows(pk, out, store, cfg))

    if rows:
        store.upsert("predictions", rows)
        Path(args.output_json).write_text(json.dumps(rows, indent=2, default=str))
        log.info("Wrote %d predictions → %s", len(rows), args.output_json)

    print(render_ledger_section(store, season=int(args.date[:4])) or
          f"YTD LEDGER ({args.date[:4]}): no settled picks yet")
    _print_top_board(
        side_rows or rows, args.date,
        baseline_fallback=bundle is None,
        odds_status=odds_status,
    )
    return 0


def _annotate_conviction(rows: list[dict]) -> None:
    """Attach shared conviction color fields to daily rows in-place."""
    ordered = sorted(rows, key=_row_sort_strength, reverse=True)
    candidates = [
        {
            "model_probability": float(r.get("nrfi_prob", 0.0)),
            "edge": r.get("edge"),
        }
        for r in ordered
    ]
    electric = electric_indices(candidates, top_n=3, min_probability=0.58)
    electric_ids = {id(ordered[idx]) for idx in electric}
    for row in rows:
        p = float(row.get("nrfi_prob", 0.0))
        band = conviction_band(
            p,
            edge=row.get("edge"),
            is_electric=id(row) in electric_ids,
        )
        row["conviction_color"] = band.label
        row["conviction_hex"] = band.hex_color
        row["conviction_rank"] = band.rank


def _row_sort_strength(row: dict) -> float:
    """Rank by edge when available, otherwise highest NRFI probability."""
    edge = row.get("edge")
    if edge is not None:
        return float(edge)
    return _side_probability(row)


def _side_probability(row: dict) -> float:
    if "side_probability" in row:
        return float(row.get("side_probability") or 0.0)
    return float(row.get("nrfi_prob", 0.0))


def _implied_prob(american_odds: float) -> float:
    if american_odds < 0:
        return abs(float(american_odds)) / (abs(float(american_odds)) + 100.0)
    return 100.0 / (float(american_odds) + 100.0)


def _decode_driver_text(row: dict) -> str:
    raw = row.get("driver_text") or ""
    if isinstance(raw, list):
        return ", ".join(str(x) for x in raw[:3])
    if isinstance(raw, str) and raw:
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return ", ".join(str(x) for x in val[:3])
        except Exception:
            return raw
    return ""


def _side_rows_from_predictions(
    predictions,
    *,
    yrfi_market_probs: list[float | None],
    yrfi_american_odds: list[float | None],
    config,
) -> list[dict]:
    """Build display-only NRFI + YRFI rows from canonical predictions."""

    rows: list[dict] = []
    for idx, pred in enumerate(predictions):
        nrfi = pred.as_row()
        nrfi["market_type"] = "NRFI"
        nrfi["side_probability"] = pred.nrfi_prob
        rows.append(nrfi)

        yrfi_p = 1.0 - float(pred.nrfi_prob)
        odds = yrfi_american_odds[idx] if idx < len(yrfi_american_odds) else None
        market_prob = yrfi_market_probs[idx] if idx < len(yrfi_market_probs) else None
        out = build_output(
            game_id=str(pred.game_pk),
            blended_p=float(pred.nrfi_prob),
            lambda_total=float(pred.lambda_total),
            market_type="YRFI",
            shap_drivers=pred.shap_drivers,
            mc_low=pred.mc_low,
            mc_high=pred.mc_high,
            market_prob=market_prob,
            market_american_odds=odds,
            engine="ml",
            model_version=pred.model_version,
            kelly_fraction=config.betting.kelly_fraction,
            min_edge=config.betting.min_edge_to_bet,
            vig_buffer=config.betting.vig_buffer,
            max_stake_units=config.betting.max_stake_units,
        )
        rows.append({
            "game_pk": pred.game_pk,
            "market_type": "YRFI",
            "nrfi_prob": out.nrfi_prob,
            "nrfi_pct": out.nrfi_pct,
            "side_probability": yrfi_p,
            "lambda_total": out.lambda_total,
            "color_band": out.color_band,
            "color_hex": out.color_hex,
            "signal": out.signal,
            "mc_low": out.mc_low,
            "mc_high": out.mc_high,
            "mc_band_pp": out.mc_band_pp,
            "shap_drivers": json.dumps(out.shap_drivers),
            "driver_text": json.dumps(out.driver_text),
            "market_prob": out.market_prob,
            "edge": out.edge,
            "edge_pp": out.edge_pp,
            "kelly_units": out.kelly_units,
            "kelly_suggestion": out.kelly_suggestion,
            "tier": out.tier,
            "tier_basis": out.tier_basis,
            "tier_value": out.tier_value,
            "tier_band": out.tier_band,
            "probability_display": out.headline(),
            "model_version": out.model_version,
        })
    return rows


def _baseline_side_rows(game_pk: int, out, store: NRFIStore, config) -> list[dict]:
    nrfi_row = {
        "game_pk": game_pk,
        "market_type": "NRFI",
        "nrfi_prob": out.nrfi_prob,
        "nrfi_pct": out.nrfi_pct,
        "lambda_total": out.lambda_total,
        "color_band": out.color_band,
        "mc_band_pp": out.mc_band_pp,
        "edge": out.edge,
        "edge_pp": out.edge_pp,
        "kelly_units": out.kelly_units,
        "kelly_suggestion": out.kelly_suggestion,
        "tier": out.tier,
        "driver_text": json.dumps(out.driver_text),
    }
    yrfi_odds = lookup_closing_odds(store, int(game_pk), "YRFI")
    yrfi = build_output(
        game_id=str(game_pk),
        blended_p=out.nrfi_prob,
        lambda_total=out.lambda_total,
        market_type="YRFI",
        market_american_odds=yrfi_odds,
        engine="poisson_baseline",
        model_version=out.model_version,
        kelly_fraction=config.betting.kelly_fraction,
        min_edge=config.betting.min_edge_to_bet,
        vig_buffer=config.betting.vig_buffer,
        max_stake_units=config.betting.max_stake_units,
    )
    yrfi_row = {
        "game_pk": game_pk,
        "market_type": "YRFI",
        "nrfi_prob": yrfi.nrfi_prob,
        "nrfi_pct": yrfi.nrfi_pct,
        "lambda_total": yrfi.lambda_total,
        "color_band": yrfi.color_band,
        "mc_band_pp": yrfi.mc_band_pp,
        "edge": yrfi.edge,
        "edge_pp": yrfi.edge_pp,
        "kelly_units": yrfi.kelly_units,
        "kelly_suggestion": yrfi.kelly_suggestion,
        "tier": yrfi.tier,
        "driver_text": json.dumps(yrfi.driver_text),
    }
    return [nrfi_row, yrfi_row]


class LiveOddsStatus:
    """Small status object for daily report transparency."""

    def __init__(
        self,
        *,
        nrfi_snapshots: int = 0,
        props_games: int = 0,
        odds_api_available: bool = False,
        message: str = "",
    ):
        self.nrfi_snapshots = nrfi_snapshots
        self.props_games = props_games
        self.odds_api_available = odds_api_available
        self.message = message


def _pull_live_odds(store: NRFIStore, game_date: str, *, config) -> LiveOddsStatus:
    """Best-effort live market pull for today's daily run.

    NRFI/YRFI 0.5 lines are persisted into DuckDB for edge/Kelly and later
    ledger settlement. Player props are fetched as a smoke-check/count only
    until the props engine owns its model/output layer.
    """
    if not os.environ.get("THE_ODDS_API_KEY"):
        return LiveOddsStatus(message="Odds API key unavailable")

    nrfi_snapshots = capture_closing_lines(store, game_date, config=config)
    props_games = _pull_player_prop_odds_count()
    return LiveOddsStatus(
        nrfi_snapshots=nrfi_snapshots,
        props_games=props_games,
        odds_api_available=True,
        message="live pull complete",
    )


def _pull_player_prop_odds_count() -> int:
    """Return count of MLB games with prop odds available from The Odds API."""
    try:
        from edge_equation.engines.props_prizepicks.source.odds_api import (
            MLB_PROPS_MARKETS,
            SPORT_KEY_MLB,
        )
        import httpx

        api_key = os.environ["THE_ODDS_API_KEY"]
        url = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY_MLB}/odds"
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params={
                "apiKey": api_key,
                "regions": "us",
                "markets": ",".join(MLB_PROPS_MARKETS),
                "oddsFormat": "american",
                "dateFormat": "iso",
            })
            resp.raise_for_status()
            payload = resp.json()
        return len(payload) if isinstance(payload, list) else 0
    except Exception as exc:
        log.warning("player prop odds pull skipped (%s): %s", type(exc).__name__, exc)
        return 0


def _print_top_board(
    rows: list[dict],
    game_date: str,
    *,
    baseline_fallback: bool = False,
    odds_status: LiveOddsStatus | None = None,
) -> None:
    """Print a compact production board: Top 6 by edge-strength."""
    _annotate_conviction(rows)
    ranked = sorted(rows, key=_row_sort_strength, reverse=True)[:6]
    print(f"\n=== NRFI Elite Board {game_date} - Top 6 by edge ===")
    if baseline_fallback:
        print("DISCLAIMER: trained calibrated bundle unavailable; using Poisson baseline.")
    if odds_status is not None:
        if odds_status.odds_api_available:
            print(
                "Odds API: "
                f"{odds_status.nrfi_snapshots} NRFI/YRFI snapshots, "
                f"{odds_status.props_games} prop games"
            )
        else:
            print(f"Odds API: unavailable ({odds_status.message})")
    for idx, r in enumerate(ranked, start=1):
        side = "NRFI"
        label = f"game_pk={int(r['game_pk'])} {side}"
        band_label = str(r.get("conviction_color") or r.get("color_band") or "")
        model_p = float(r.get("nrfi_prob", 0.0))
        mc = (
            f"+/-{float(r['mc_band_pp']):.1f}pp"
            if r.get("mc_band_pp") is not None else "MC --"
        )
        edge = (
            f"edge {float(r['edge_pp']):+.1f}pp"
            if r.get("edge_pp") is not None else "edge n/a"
        )
        drivers = _decode_driver_text(r) or "model drivers pending"
        tier = str(r.get("tier", ""))
        color = f"{tier} ({band_label})" if tier else band_label
        print(
            f"{idx:>2}. "
            f"{format_conviction_line(label=label, model_probability=model_p, band=conviction_band(model_p, edge=r.get('edge'), is_electric=band_label == 'Electric Blue'), stake_units=r.get('kelly_units') if r.get('kelly_units') else None)}"
        )
        print(
            f"    {color:<24} "
            f"lambda={float(r['lambda_total']):.2f}  {mc:<8} {edge:<10} "
            f"Kelly={r.get('kelly_suggestion') or 'Market unavailable'}"
        )
        print(f"    Why: {drivers}")
    print()
    print(render_conviction_key())


if __name__ == "__main__":
    raise SystemExit(main())
