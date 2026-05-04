"""
Backtest CLI — exposes the v1 walk-forward engine over scrapers'
`picks_log.json` history so we can run side-by-side regression
diagnoses ("v1-current" vs "v1-with-negbin") against the exact same
historical bets.

Usage
-----
Replay v1's CURRENT model over the harvested bet log:

    python -m edge_equation.backtest.cli \\
        --picks-log data/scrapers_history/mlb/picks_log.json \\
        --start 2026-04-01 --end 2026-05-02 \\
        --model v1-current \\
        --out reports/regression/v1_current.json

Replay with the new NegBin projection adapter:

    python -m edge_equation.backtest.cli ... --model v1-with-negbin

Diff two replays (third-party tooling):

    python -m edge_equation.backtest.cli diff \\
        reports/regression/v1_current.json \\
        reports/regression/v1_negbin.json \\
        --baseline data/scrapers_history/mlb/backtest.json

The output JSON matches scrapers' backtest.json shape so downstream
tools (the gate module, the workbook backtest tab) consume both
sources uniformly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


def _load_picks_log(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if isinstance(payload, dict) and "picks" in payload:
        return payload["picks"]
    if isinstance(payload, list):
        return payload
    raise SystemExit(f"unrecognized picks_log shape at {path}")


def _filter_by_window(picks: Iterable[dict], start: str, end: str) -> list[dict]:
    return [p for p in picks if start <= (p.get("date") or "") <= end]


def _summarize(picks: list[dict]) -> dict:
    """Group picks by bet_type and emit the exact schema the gate module
    consumes: bets, wins, losses, pushes, hit_rate, roi_pct, brier, clv_pct."""
    by: dict[str, dict] = {}
    for p in picks:
        bt = p.get("bet_type") or "unknown"
        slot = by.setdefault(bt, {
            "bet_type": bt,
            "bets": 0, "wins": 0, "losses": 0, "pushes": 0,
            "_units": 0.0, "_brier_acc": 0.0, "_brier_n": 0,
            "_clv_acc": 0.0, "_clv_n": 0,
        })
        slot["bets"] += 1
        result = (p.get("result") or "").upper()
        if result == "WIN":
            slot["wins"] += 1
        elif result == "LOSS":
            slot["losses"] += 1
        elif result == "PUSH":
            slot["pushes"] += 1
        slot["_units"] += float(p.get("units") or 0.0)
        prob = p.get("model_prob")
        if prob is not None and result in ("WIN", "LOSS"):
            y = 1.0 if result == "WIN" else 0.0
            slot["_brier_acc"] += (float(prob) - y) ** 2
            slot["_brier_n"] += 1
        clv = p.get("clv_pct")
        if clv is not None:
            slot["_clv_acc"] += float(clv)
            slot["_clv_n"] += 1

    summary = []
    for bt, slot in by.items():
        decided = slot["wins"] + slot["losses"]
        hit_rate = round(slot["wins"] / decided, 4) if decided else None
        roi_pct = round(slot["_units"] / slot["bets"] * 100, 2) if slot["bets"] else 0.0
        brier = round(slot["_brier_acc"] / slot["_brier_n"], 4) if slot["_brier_n"] else None
        clv_pct = round(slot["_clv_acc"] / slot["_clv_n"], 2) if slot["_clv_n"] else None
        summary.append({
            "bet_type": bt,
            "bets": slot["bets"],
            "wins": slot["wins"],
            "losses": slot["losses"],
            "pushes": slot["pushes"],
            "hit_rate": hit_rate,
            "roi_pct": roi_pct,
            "brier": brier,
            "clv_pct": clv_pct,
        })
    summary.sort(key=lambda r: r["bet_type"])
    return {
        "summary_by_bet_type": summary,
        "overall": {
            "bets": sum(r["bets"] for r in summary),
            "roi_pct": (
                round(sum((r["roi_pct"] or 0) * r["bets"] for r in summary)
                      / max(1, sum(r["bets"] for r in summary)), 2)
            ),
        },
    }


def _replay(picks_log: Path, start: str, end: str, model: str, out: Path) -> int:
    """Replay picks for the requested window with the named model.

    For now `model` is a label only — the picks_log already contains the
    realized model_prob and result. Once the v1 NegBin adapter ships,
    this function will re-project model_prob from the projection model
    of choice and recompute the summary against the same realized
    outcomes. That is what makes the side-by-side meaningful.
    """
    raw = _load_picks_log(picks_log)
    picks = _filter_by_window(raw, start, end)
    if not picks:
        print(f"No picks in window {start}..{end}", file=sys.stderr)
        return 2
    summary = _summarize(picks)
    summary["model"] = model
    summary["window"] = {"start": start, "end": end}
    summary["n_picks"] = len(picks)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(f"wrote {out}  ({len(picks)} picks, {len(summary['summary_by_bet_type'])} markets)")
    return 0


def _diff(left: Path, right: Path, baseline: Path | None) -> int:
    a = json.loads(left.read_text())
    b = json.loads(right.read_text())
    base = json.loads(baseline.read_text()) if baseline else None
    by_bt = lambda payload: {r["bet_type"]: r for r in payload.get("summary_by_bet_type", [])}
    a_map, b_map = by_bt(a), by_bt(b)
    base_map = by_bt(base) if base else {}
    print(f"{'market':<14} {'A roi':>8} {'B roi':>8} {'Δroi':>8} {'A brier':>8} {'B brier':>8}")
    for bt in sorted(set(a_map) | set(b_map)):
        ra = a_map.get(bt, {})
        rb = b_map.get(bt, {})
        droi = (rb.get("roi_pct") or 0) - (ra.get("roi_pct") or 0)
        print(f"{bt:<14} "
              f"{ra.get('roi_pct', 0):>+7.2f}% "
              f"{rb.get('roi_pct', 0):>+7.2f}% "
              f"{droi:>+7.2f}% "
              f"{(ra.get('brier') or 0):>8.4f} "
              f"{(rb.get('brier') or 0):>8.4f}")
        if base_map:
            rbase = base_map.get(bt, {})
            print(f"{'  baseline':<14} "
                  f"{rbase.get('roi_pct', 0):>+7.2f}% "
                  f"{'':>9} {'':>9} "
                  f"{(rbase.get('brier') or 0):>8.4f}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="edge_equation.backtest.cli")
    sub = p.add_subparsers(dest="cmd")

    rep = sub.add_parser("replay", help="replay a model over a window")
    rep.add_argument("--picks-log", type=Path, required=True)
    rep.add_argument("--start", required=True)
    rep.add_argument("--end", required=True)
    rep.add_argument("--model", default="v1-current")
    rep.add_argument("--out", type=Path, required=True)

    df = sub.add_parser("diff", help="compare two replay outputs")
    df.add_argument("left", type=Path)
    df.add_argument("right", type=Path)
    df.add_argument("--baseline", type=Path, default=None)

    # Top-level shortcut: if no subcommand, default to replay.
    p.add_argument("--picks-log", type=Path)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--model")
    p.add_argument("--out", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "diff":
        return _diff(args.left, args.right, args.baseline)
    if args.cmd == "replay" or args.picks_log:
        picks_log = getattr(args, "picks_log", None) or args.picks_log
        start = getattr(args, "start", None)
        end = getattr(args, "end", None)
        out = getattr(args, "out", None)
        model = getattr(args, "model", None) or "v1-current"
        if not (picks_log and start and end and out):
            parser.error("replay requires --picks-log, --start, --end, --out")
        return _replay(picks_log, start, end, model, out)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
