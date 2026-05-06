"""
WNBA Daily — orchestrator.

Mirrors src/edge_equation/exporters/mlb/daily_spreadsheet.py at a
high level but emits JSON only (no xlsx, no per-tab CSV layer). The
13-step MLB pipeline collapses to:

    1. Pull season-to-date backfill (multi-season games.json on disk
       + current-season games as they accumulate).
    2. Pull today's slate from ESPN scoreboard.
    3. Pull market odds (Odds API basketball_wnba) when available.
    4. Run rolling backtest → calibration block + per-market summary.
    5. Build ProjectionModel with calibrated SDs / slope.
    6. Project today's slate.
    7. Apply BRAND_GUIDE market gate (200 bets / +1% ROI / Brier
       <0.250 — slightly looser than MLB's 0.246 since basketball
       totals are inherently noisier).
    8. Compute edges + Kelly per surviving pick.
    9. Write JSON outputs to website/public/data/wnba/.

Outputs (4 files):
    wnba_daily.json     — the full data dump (slate, projections,
                          gate decisions, today's card)
    backtest.json       — rolling per-market summary
    calibration.json    — fitted SDs + slope
    todays_card.json    — picks that cleared the gate (or empty
                          when the gate's still cold-starting)

Feature flag: EDGE_FEATURE_WNBA_PIPELINE=on. Off by default. The cron
flips it on; local runs default to off so an accidental run doesn't
hit the Odds API or write to the website's deployed dir.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from edge_equation.exporters.wnba import gates
from edge_equation.exporters.wnba.backtest import (
    BacktestEngine, FLAT_DECIMAL_ODDS, SPREAD_LINE,
)
from edge_equation.exporters.wnba.projections import (
    ProjectionModel, prob_over, prob_total_over_under,
)
from edge_equation.exporters.wnba.slate import fetch_slate
from edge_equation.exporters.wnba._odds_adapter import WNBAOddsScraper
from edge_equation.exporters.mlb.kelly import (
    american_to_decimal, kelly_advice, edge_pct,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "website" / "public" / "data" / "wnba"
DEFAULT_BACKFILL_DIR = REPO_ROOT / "data" / "backfill" / "wnba"

FEATURE_FLAG_ENV = "EDGE_FEATURE_WNBA_PIPELINE"
SEASON_DEFAULT = 2026


def _flag_on() -> bool:
    return os.environ.get(FEATURE_FLAG_ENV, "").strip().lower() in (
        "1", "on", "true", "yes",
    )


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _discover_backfill_seasons(backfill_dir: Path, current_season: int) -> list[int]:
    if not backfill_dir.exists():
        return []
    out = []
    for child in backfill_dir.iterdir():
        if child.is_dir() and child.name.isdigit():
            if (child / "games.json").exists():
                season = int(child.name)
                if season != current_season:  # current goes through live load
                    out.append(season)
    return sorted(out)


def _load_current_season_games(backfill_dir: Path, season: int) -> list[dict]:
    """Best-effort load of the current season's games. The 2026
    games.json gets refreshed by an external backfill workflow once
    games complete. If the file doesn't exist or is empty, return
    empty — projector cold-starts gracefully off prior seasons."""
    p = backfill_dir / str(season) / "games.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _build_pick(
    proj: dict, odds_game: dict | None, bet_type: str,
    side: str, prob: float, line: float | None,
    decimal_odds: float, american_odds: int | None, book: str | None,
) -> dict:
    """Render a single pick row."""
    adv = kelly_advice(prob, decimal_odds=decimal_odds)
    e_pct = edge_pct(prob, decimal_odds)
    return {
        "date": proj.get("date"),
        "matchup": f"{proj['away_team']}@{proj['home_team']}",
        "bet_type": bet_type,
        "pick": f"{side} {line}" if line is not None else side,
        "model_prob": round(prob, 3),
        "fair_odds_dec": round(1 / prob, 3) if 0 < prob < 1 else None,
        "decimal_odds": decimal_odds,
        "american_odds": american_odds,
        "book": book,
        "edge_pct": e_pct,
        "kelly_pct": adv["kelly_pct"],
        "kelly_advice": adv["kelly_advice"],
    }


def _build_picks(projections: list[dict], odds_payload: dict | None,
                 cal: dict) -> list[dict]:
    """Build today's actionable picks across moneyline, spread, totals.
    For each game produces one pick per market when odds are available
    (so the gate has consistent denominators). Falls back to flat -110
    when no odds are present so the engine still emits picks (Kelly
    will be lower but the operator sees what the model thinks)."""
    picks: list[dict] = []
    odds_games = (odds_payload or {}).get("games", []) if odds_payload else []

    for proj in projections:
        away, home = proj["away_team"], proj["home_team"]
        odds_game = WNBAOddsScraper.find_game(
            odds_payload or {"games": odds_games}, away, home,
        ) if odds_payload else None

        # Moneyline — pick the projected favorite
        ml_pick_home = proj["home_win_prob"] >= 0.5
        ml_team = home if ml_pick_home else away
        ml_prob = proj["home_win_prob"] if ml_pick_home else proj["away_win_prob"]
        if odds_game and (ml_side := odds_game["moneyline"].get(
                "home" if ml_pick_home else "away")):
            picks.append(_build_pick(
                proj, odds_game, "moneyline", ml_team, ml_prob, None,
                ml_side["decimal"], ml_side["american"], ml_side["book"],
            ))
        else:
            picks.append(_build_pick(
                proj, None, "moneyline", ml_team, ml_prob, None,
                FLAT_DECIMAL_ODDS, -110, None,
            ))

        # Spread — bet the projected underdog at +SPREAD_LINE.
        # P(underdog covers) = P(fav margin <= SPREAD_LINE)
        fav_is_home = proj["margin_proj"] >= 0
        underdog = away if fav_is_home else home
        cover_prob = 1.0 - prob_over(
            SPREAD_LINE, abs(proj["margin_proj"]),
            cal.get("margin_sd") or 12.0,
        )
        line_for_pick = SPREAD_LINE
        if odds_game and odds_game["spread"]:
            # Find the underdog side's actual book line + price
            target_side = "away" if fav_is_home else "home"
            spread_offer = next(
                (s for s in odds_game["spread"] if s["team"] == target_side),
                None,
            )
            if spread_offer is not None:
                # Use the book's actual line for the cover-prob calc
                line_for_pick = abs(spread_offer["point"])
                cover_prob = 1.0 - prob_over(
                    line_for_pick, abs(proj["margin_proj"]),
                    cal.get("margin_sd") or 12.0,
                )
                picks.append(_build_pick(
                    proj, odds_game, "spread", underdog, cover_prob,
                    f"+{line_for_pick}",
                    spread_offer["decimal"], spread_offer["american"],
                    spread_offer["book"],
                ))
                continue
        picks.append(_build_pick(
            proj, None, "spread", underdog, cover_prob,
            f"+{line_for_pick}", FLAT_DECIMAL_ODDS, -110, None,
        ))

        # Totals — best Over/Under at the book's line (or canonical
        # 162.5 if no book is offering).
        total_offers = (odds_game or {}).get("totals", []) if odds_game else []
        if total_offers:
            best = None
            for offer in total_offers:
                line = offer["point"]
                p_over, p_under, _ = prob_total_over_under(
                    line, proj["total_proj"], cal.get("total_sd") or 14.0,
                )
                for side_key, side_label, prob in (
                    ("over", "OVER", p_over), ("under", "UNDER", p_under),
                ):
                    price = offer.get(side_key)
                    if not price:
                        continue
                    cand_edge = edge_pct(prob, price["decimal"])
                    if best is None or (
                        cand_edge is not None
                        and (best.get("edge_pct") or -1e9) < cand_edge
                    ):
                        best = {
                            "side": side_label, "line": line, "prob": prob,
                            "decimal": price["decimal"],
                            "american": price["american"],
                            "book": price["book"],
                            "edge_pct": cand_edge,
                        }
            if best is not None:
                picks.append(_build_pick(
                    proj, odds_game, "totals", best["side"], best["prob"],
                    best["line"], best["decimal"], best["american"], best["book"],
                ))
                continue
        # No odds — emit canonical line at flat juice
        line = 162.5
        p_over, p_under, _ = prob_total_over_under(
            line, proj["total_proj"], cal.get("total_sd") or 14.0,
        )
        if p_over >= p_under:
            picks.append(_build_pick(
                proj, None, "totals", "OVER", p_over, line,
                FLAT_DECIMAL_ODDS, -110, None,
            ))
        else:
            picks.append(_build_pick(
                proj, None, "totals", "UNDER", p_under, line,
                FLAT_DECIMAL_ODDS, -110, None,
            ))
    return picks


def _apply_gate(
    picks: list[dict], passed_markets: set[str] | None,
    edge_overrides: dict[str, float] | None,
) -> tuple[list[dict], dict[str, str]]:
    """Filter picks down to today's card. A pick survives iff
    (a) its bet_type cleared the rolling-window market gate, AND
    (b) its edge_pct meets or beats the per-market floor.
    """
    out: list[dict] = []
    drop_notes: dict[str, str] = {}
    for p in picks:
        bt = p.get("bet_type")
        if passed_markets is not None and bt not in passed_markets:
            drop_notes[f"{p['matchup']} {bt}"] = "market gated off"
            continue
        floor = gates.edge_floor_for(bt, overrides=edge_overrides)
        e = p.get("edge_pct")
        if e is None or e < floor:
            drop_notes[f"{p['matchup']} {bt}"] = (
                f"edge {e}% below {floor}% floor"
            )
            continue
        out.append(p)
    return out, drop_notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge_equation.exporters.wnba.daily")
    parser.add_argument("--season", type=int, default=SEASON_DEFAULT)
    parser.add_argument("--date", default=None,
                        help="Target date YYYY-MM-DD (defaults today UTC)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--backfill-dir", type=Path, default=DEFAULT_BACKFILL_DIR)
    parser.add_argument("--no-odds", action="store_true", default=False)
    parser.add_argument("--odds-api-key", type=str, default=None)
    parser.add_argument("--include-backfill", action="store_true", default=True,
                        help="Multi-season backtest (default on)")
    parser.add_argument("--gate-min-bets", type=int, default=gates.MIN_GATE_BETS)
    parser.add_argument("--gate-min-roi", type=float, default=gates.MIN_GATE_ROI)
    parser.add_argument("--gate-max-brier", type=float, default=gates.MAX_GATE_BRIER)
    parser.add_argument("--no-market-gate", action="store_true", default=False)
    parser.add_argument("--edge-threshold", action="append", default=[],
                        metavar="MARKET=PCT")
    args = parser.parse_args(argv)

    if not _flag_on():
        print(
            f"[wnba-daily] feature flag {FEATURE_FLAG_ENV}=off — skipping. "
            f"Set {FEATURE_FLAG_ENV}=on to run.",
            file=sys.stderr,
        )
        return 0

    target_date = args.date or _today_utc()
    overrides = gates.parse_threshold_overrides(args.edge_threshold)

    print(f"WNBA Daily — target {target_date}")

    # 1. Backtest sample (multi-season)
    seasons = (
        _discover_backfill_seasons(args.backfill_dir, args.season)
        if args.include_backfill else []
    )
    current = _load_current_season_games(args.backfill_dir, args.season)
    print(f"  seasons {seasons} on disk + current ({len(current)} games)")
    backtest_engine = BacktestEngine.from_multi_season(
        backfill_dir=args.backfill_dir,
        seasons=seasons,
        current_season_games=current,
    )
    print(f"  Combined sample: {len(backtest_engine.games):,} games "
          f"({getattr(backtest_engine, '_seasons_loaded', {})})")
    backtest = backtest_engine.run()
    cal = backtest["calibration"]
    print(f"    {backtest['overall']['bets']:,} graded bets, "
          f"hit rate {backtest['overall']['hit_rate']}%, "
          f"units {backtest['overall']['units_pl']:+.2f}")
    print(f"    calibrated SDs: total {cal['total_sd']}, "
          f"margin {cal['margin_sd']}, team {cal['team_pts_sd']}, "
          f"slope {cal['win_prob_slope']}")

    # 2. Today's slate
    print(f"  Fetching slate for {target_date}...")
    slate = fetch_slate(target_date)
    print(f"    {len(slate)} scheduled game(s)")

    # 3. Odds (skip on --no-odds)
    odds_payload = None
    if not args.no_odds and slate:
        try:
            print("  Fetching market odds (basketball_wnba)...")
            odds_payload = WNBAOddsScraper(
                api_key=args.odds_api_key,
                quota_log_path=args.output_dir / "quota_log.json",
            ).fetch()
            print(f"    {len(odds_payload.get('games', []))} priced game(s)")
        except Exception as e:
            print(f"    odds fetch failed ({type(e).__name__}: {e}); "
                  f"falling back to flat -110")

    # 4. Project today's slate using calibrated constants
    print("  Building projection model with calibrated constants...")
    model = ProjectionModel(backtest_engine.games, calibration=cal)
    projections = model.project_slate(slate)

    # 5. Build raw pick rows (one per market per game)
    raw_picks = _build_picks(projections, odds_payload, cal)
    print(f"    {len(raw_picks)} raw pick row(s) built")

    # 6. Apply BRAND_GUIDE gate
    if args.no_market_gate:
        passed_markets, gate_notes = None, {}
    else:
        passed_markets, gate_notes = gates.market_gate(
            backtest.get("summary_by_bet_type"),
            min_bets=args.gate_min_bets,
            min_roi=args.gate_min_roi,
            max_brier=args.gate_max_brier,
        )
    print(f"  Market gate: passed={list(passed_markets) if passed_markets else 'cold-start (all allowed)'}")
    if gate_notes:
        for k, v in gate_notes.items():
            print(f"    excluded {k}: {v}")

    todays_card, drop_notes = _apply_gate(raw_picks, passed_markets, overrides)
    print(f"  Today's card: {len(todays_card)} pick(s) after edge floor + gate")

    # 7. Write JSON outputs
    args.output_dir.mkdir(parents=True, exist_ok=True)
    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "as_of": as_of,
        "date": target_date,
        "season": args.season,
        "seasons_loaded": getattr(backtest_engine, "_seasons_loaded", {}),
        "slate": slate,
        "projections": projections,
        "all_picks": raw_picks,
        "todays_card": todays_card,
        "passed_markets": list(passed_markets) if passed_markets is not None else None,
        "gate_notes": gate_notes,
        "drop_notes": drop_notes,
    }
    (args.output_dir / "wnba_daily.json").write_text(
        json.dumps(payload, indent=2)
    )
    (args.output_dir / "backtest.json").write_text(
        json.dumps({
            "as_of": as_of,
            "summary_by_bet_type": backtest["summary_by_bet_type"],
            "overall": backtest["overall"],
            "first_date": backtest["first_date"],
            "last_date": backtest["last_date"],
        }, indent=2)
    )
    (args.output_dir / "calibration.json").write_text(
        json.dumps(cal, indent=2)
    )
    (args.output_dir / "todays_card.json").write_text(
        json.dumps({
            "as_of": as_of,
            "date": target_date,
            "picks": todays_card,
        }, indent=2)
    )
    print(f"  Wrote 4 JSON files to {args.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
