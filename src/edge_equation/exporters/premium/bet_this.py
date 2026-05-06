"""Premium "Bet This" combined spreadsheet exporter.

Reads each per-sport daily JSON the per-sport exporter already
produces and merges high-conviction PLAY rows into a single premium
deliverable:

    website/public/data/premium/bet_this.json
    website/public/data/premium/bet_this.csv
    website/public/data/premium/bet_this.xlsx (when openpyxl is present)

Sources today:
    MLB:  website/public/data/mlb/mlb_daily.json
          (per-tab `projections` rows that the MLB exporter already
          tagged with status=PLAY/PASS, conviction_pct, edge_pct,
          kelly_pct).
    WNBA: website/public/data/wnba/wnba_daily.json
          (`todays_card` array of post-gate picks).

Picks that survive into the premium card must clear an elevated bar on
top of the per-sport gates already applied:

    1. Status == PLAY (per-sport BRAND_GUIDE gate already passed)
    2. Conviction (model_prob) >= MIN_PREMIUM_CONVICTION (default 60%)
    3. Edge >= MIN_PREMIUM_EDGE_PCT (default 4.5%)
    4. Kelly advice >= 1u (i.e. not "PASS" or fractional)

Modular by design: adding a new sport (NBA, NHL) is a single new
`_load_<sport>_picks` function that returns a list of normalised
``PremiumPick`` rows. The combiner / writer is sport-agnostic.

Usage::

    python -m edge_equation.exporters.premium.bet_this
    python -m edge_equation.exporters.premium.bet_this --date 2026-05-09
    python -m edge_equation.exporters.premium.bet_this --min-conviction 65 \\
        --min-edge 5.0
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MLB_JSON = REPO_ROOT / "website" / "public" / "data" / "mlb" / "mlb_daily.json"
DEFAULT_WNBA_JSON = REPO_ROOT / "website" / "public" / "data" / "wnba" / "wnba_daily.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "website" / "public" / "data" / "premium"

# BRAND_GUIDE-aligned premium thresholds. The per-sport exporter has
# already enforced its market gate; these are the *additional* "only the
# meatiest plays" filters.
MIN_PREMIUM_CONVICTION = 60.0   # %
MIN_PREMIUM_EDGE_PCT   = 4.5    # %
MIN_PREMIUM_KELLY_PCT  = 1.0    # %  (skip Kelly < 1u plays)


@dataclass
class PremiumPick:
    """Normalised cross-sport row. Whatever sport-specific fields the
    source carries land in `extras`; the table emitted to CSV/XLSX uses
    the canonical columns below.
    """
    sport: str
    matchup: str
    market: str
    side: str
    line: Optional[str]
    conviction_pct: float
    edge_pct: float
    kelly_pct: float
    kelly_advice: str
    market_odds_american: Optional[int]
    book: Optional[str]
    bet_this: str = ""           # the explicit "Bet This: ..." sentence
    extras: Dict[str, Any] = field(default_factory=dict)


def _fmt_american(am: Any) -> Optional[int]:
    if am is None or am == "":
        return None
    try:
        return int(round(float(am)))
    except (TypeError, ValueError):
        return None


def _bet_this_sentence(pick: PremiumPick) -> str:
    """The brand-aligned 'Facts. Not Feelings.' action line."""
    line_part = f" {pick.line}" if pick.line else ""
    odds = (
        f" @ {pick.market_odds_american:+d}" if pick.market_odds_american
        else ""
    )
    book = f" ({pick.book})" if pick.book else ""
    kelly = f" - Stake: {pick.kelly_advice}" if pick.kelly_advice else ""
    return (
        f"Bet This: {pick.matchup} - {pick.market.upper()} "
        f"{pick.side}{line_part}{odds}{book}{kelly}"
    )


# ---------------------------------------------------------------------
# MLB loader
# ---------------------------------------------------------------------

_MLB_TAB_LABELS = {
    "moneyline":   "Moneyline",
    "run_line":    "Run Line",
    "totals":      "Totals",
    "first_5":     "First 5",
    "first_inning": "First Inning",
    "team_totals": "Team Totals",
}


def load_mlb_picks(path: Path = DEFAULT_MLB_JSON) -> List[PremiumPick]:
    """Read MLB per-tab projections and emit one PremiumPick per row
    where status == PLAY. Pulls conviction / edge / Kelly straight from
    the row -- the MLB exporter already calibrated them and inserted
    `status` via `_inject_status_columns`."""
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    out: List[PremiumPick] = []
    for tab_key, tab in (payload.get("tabs") or {}).items():
        if tab_key not in _MLB_TAB_LABELS:
            continue
        market_label = _MLB_TAB_LABELS[tab_key]
        for row in tab.get("projections") or []:
            if (row.get("status") or "").upper() != "PLAY":
                continue
            matchup = f"{row.get('away')}@{row.get('home')}"
            side, line_str = _extract_mlb_side_and_line(tab_key, row)
            out.append(PremiumPick(
                sport="MLB",
                matchup=matchup,
                market=market_label,
                side=side,
                line=line_str,
                conviction_pct=float(row.get("conviction_pct") or 0.0),
                edge_pct=float(row.get("edge_pct") or 0.0),
                kelly_pct=float(row.get("kelly_pct") or 0.0),
                kelly_advice=str(row.get("kelly_advice") or ""),
                market_odds_american=_fmt_american(
                    row.get("market_odds_american")
                ),
                book=row.get("book"),
                extras={
                    "model_prob": row.get("model_prob"),
                    "fair_odds_dec": row.get("fair_odds_dec"),
                    "ml_pick": row.get("ml_pick"),
                    "away_runs_proj": row.get("away_runs_proj"),
                    "home_runs_proj": row.get("home_runs_proj"),
                    "date": row.get("date"),
                },
            ))
    return out


def _extract_mlb_side_and_line(
    tab_key: str, row: Dict[str, Any],
) -> tuple[str, Optional[str]]:
    """Best-effort label for the side + line shown to the user.
    Different MLB tabs encode the pick a little differently -- this
    centralises the formatting so the premium spreadsheet has a clean
    `side` column. Falls back to whatever raw fields are present.
    """
    if tab_key == "moneyline":
        return str(row.get("ml_pick") or "—"), None
    if tab_key == "run_line":
        side = str(row.get("rl_pick") or row.get("ml_pick") or "—")
        return f"{side} +1.5", "+1.5"
    if tab_key in ("totals", "first_5"):
        side = str(row.get("ou_pick") or row.get("pick_side") or "OVER")
        line = (
            row.get("ou_line") or row.get("line") or row.get("total_line")
        )
        return side, str(line) if line is not None else None
    if tab_key == "first_inning":
        return str(row.get("nrfi_pick") or "NRFI"), None
    if tab_key == "team_totals":
        team = row.get("team") or row.get("away") or row.get("home") or ""
        side = str(row.get("ou_pick") or "OVER")
        line = row.get("ou_line") or row.get("line")
        return f"{team} {side}", str(line) if line is not None else None
    return str(row.get("pick") or "—"), None


# ---------------------------------------------------------------------
# WNBA loader
# ---------------------------------------------------------------------

def load_wnba_picks(path: Path = DEFAULT_WNBA_JSON) -> List[PremiumPick]:
    """Read WNBA `todays_card` (already gate-filtered) and emit
    PremiumPick rows. The exporter writes Kelly + edge directly so
    we mirror them."""
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    out: List[PremiumPick] = []
    for pick in payload.get("todays_card") or []:
        bt = pick.get("bet_type") or ""
        market_label = {
            "moneyline": "Moneyline",
            "spread":    "Spread",
            "totals":    "Total",
        }.get(bt, bt.title())
        out.append(PremiumPick(
            sport="WNBA",
            matchup=str(pick.get("matchup") or ""),
            market=market_label,
            side=str(pick.get("pick") or "—"),
            line=None,
            conviction_pct=round(float(pick.get("model_prob") or 0.0) * 100, 1),
            edge_pct=float(pick.get("edge_pct") or 0.0),
            kelly_pct=float(pick.get("kelly_pct") or 0.0),
            kelly_advice=str(pick.get("kelly_advice") or ""),
            market_odds_american=_fmt_american(pick.get("american_odds")),
            book=pick.get("book"),
            extras={
                "fair_odds_dec": pick.get("fair_odds_dec"),
                "decimal_odds": pick.get("decimal_odds"),
                "date": pick.get("date"),
            },
        ))
    return out


# ---------------------------------------------------------------------
# Combiner + filter
# ---------------------------------------------------------------------

def filter_premium(
    picks: Iterable[PremiumPick],
    min_conviction: float = MIN_PREMIUM_CONVICTION,
    min_edge_pct: float = MIN_PREMIUM_EDGE_PCT,
    min_kelly_pct: float = MIN_PREMIUM_KELLY_PCT,
) -> List[PremiumPick]:
    """Apply the elevated 'Bet This' bar. Returns sorted descending by
    Kelly% so the highest-conviction stake size leads the spreadsheet."""
    out: List[PremiumPick] = []
    for p in picks:
        if p.conviction_pct < min_conviction:
            continue
        if p.edge_pct < min_edge_pct:
            continue
        if p.kelly_pct < min_kelly_pct:
            continue
        if (p.kelly_advice or "").strip().upper() == "PASS":
            continue
        # Compose the "Bet This" sentence here so the writers don't need
        # to know about brand language.
        p.bet_this = _bet_this_sentence(p)
        out.append(p)
    out.sort(key=lambda x: x.kelly_pct, reverse=True)
    return out


# ---------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------

PREMIUM_COLUMNS = [
    "sport", "matchup", "market", "side", "line",
    "conviction_pct", "edge_pct", "kelly_pct", "kelly_advice",
    "market_odds_american", "book", "bet_this",
]


def write_outputs(
    picks: List[PremiumPick], output_dir: Path,
    target_date: str, generated_at: str,
    thresholds: Dict[str, float],
) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "bet_this.json"
    csv_path = output_dir / "bet_this.csv"
    xlsx_path = output_dir / "bet_this.xlsx"

    rows = [{k: getattr(p, k) for k in PREMIUM_COLUMNS} for p in picks]
    payload = {
        "generated_at": generated_at,
        "date": target_date,
        "thresholds": thresholds,
        "n_picks": len(rows),
        "by_sport": _by_sport_counts(picks),
        "picks": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2))

    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PREMIUM_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    written = {"json": json_path, "csv": csv_path}
    if _try_write_xlsx(xlsx_path, payload, rows):
        written["xlsx"] = xlsx_path
    return written


def _by_sport_counts(picks: List[PremiumPick]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in picks:
        counts[p.sport] = counts.get(p.sport, 0) + 1
    return counts


def _try_write_xlsx(path: Path, payload: dict, rows: List[dict]) -> bool:
    try:
        from openpyxl import Workbook
    except ImportError:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "Bet This"
    # Header row
    ws.append(["Premium Card -- Facts. Not Feelings."])
    ws.append([f"Generated: {payload['generated_at']}  |  Date: {payload['date']}"])
    threshold_str = (
        f"Conviction >={payload['thresholds']['min_conviction']}%  "
        f"Edge >={payload['thresholds']['min_edge_pct']}%  "
        f"Kelly >={payload['thresholds']['min_kelly_pct']}u"
    )
    ws.append([threshold_str])
    ws.append([])
    ws.append(PREMIUM_COLUMNS)
    for r in rows:
        ws.append([r.get(c) for c in PREMIUM_COLUMNS])
    # Auto-size columns roughly
    for i, col in enumerate(PREMIUM_COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=5, column=i).column_letter].width = max(
            12, len(col) + 2,
        )
    wb.save(path)
    return True


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_equation.exporters.premium.bet_this",
        description="Build the premium 'Bet This' combined spreadsheet.",
    )
    parser.add_argument("--mlb-json", type=Path, default=DEFAULT_MLB_JSON)
    parser.add_argument("--wnba-json", type=Path, default=DEFAULT_WNBA_JSON)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--date", default=None,
                        help="Override target date label (default: today UTC)")
    parser.add_argument("--min-conviction", type=float,
                        default=MIN_PREMIUM_CONVICTION)
    parser.add_argument("--min-edge", type=float, default=MIN_PREMIUM_EDGE_PCT)
    parser.add_argument("--min-kelly", type=float, default=MIN_PREMIUM_KELLY_PCT)
    args = parser.parse_args(argv)

    target_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    mlb = load_mlb_picks(args.mlb_json)
    wnba = load_wnba_picks(args.wnba_json)
    print(f"Premium combiner: {len(mlb)} MLB PLAY rows, {len(wnba)} WNBA card rows")

    combined = filter_premium(
        mlb + wnba,
        min_conviction=args.min_conviction,
        min_edge_pct=args.min_edge,
        min_kelly_pct=args.min_kelly,
    )
    print(f"  {len(combined)} pick(s) cleared the premium bar")

    thresholds = {
        "min_conviction": args.min_conviction,
        "min_edge_pct":   args.min_edge,
        "min_kelly_pct":  args.min_kelly,
    }
    written = write_outputs(
        combined, args.output_dir, target_date, generated_at, thresholds,
    )
    for kind, path in written.items():
        print(f"  wrote {kind}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
