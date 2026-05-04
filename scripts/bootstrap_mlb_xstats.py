#!/usr/bin/env python3
"""
Bootstrap MLB prior-season xStats for the SplitsLoader.

Writes data/backfill/mlb/<season>/statcast_xstats.json in the shape
edge_equation.exporters.mlb.splits_loader.SplitsLoader expects, sourced
from MLB Stats API (statsapi.mlb.com — same source the rest of the
pipeline already uses successfully). No Savant scraping; that source
returned a CSV format that didn't parse cleanly in the first attempt
(yielded a 337-byte file with a single nonsense entry keyed on the year).

What we write
-------------
Each season's statcast_xstats.json has shape:

    {
      "season": <int>,
      "pitching": { "<player_id>": {"pa": int, "xwoba": float,
                                    "xba": float, "xslg": float}, ... },
      "batting":  { "<player_id>": { ... same fields ... }, ... }
    }

Pitcher xwOBA proxy
-------------------
We don't have actual Statcast xwOBA from MLB Stats API, but we DO have
FIP (computed from HR/BB/HBP/K/IP). FIP is calibrated to the same scale
as ERA. We map FIP onto xwOBA via a simple linear approximation tuned
to the league averages:

    xwoba_proxy = LEAGUE_XWOBA + (fip - LEAGUE_FIP) * SLOPE
    SLOPE       ≈ 0.029  (empirical: dispersion across regulars)
    bounds      [0.250, 0.380]

This isn't actual Statcast — it's a stabilizing prior derived from the
same skill (run-suppression talent) that xwOBA captures. Once a real
Savant integration lands the file's shape stays identical and only the
xwoba/xba/xslg values change.

Usage
-----
    python scripts/bootstrap_mlb_xstats.py                    # default = current_year - 1
    python scripts/bootstrap_mlb_xstats.py --season 2025
    python scripts/bootstrap_mlb_xstats.py --seasons 2024 2025
    python scripts/bootstrap_mlb_xstats.py --season 2025 --out data/backfill/mlb

Validation
----------
After writing, the script asserts:
- pitcher count >= 50 (typical season has ~80 qualified)
- every key in 'pitching' is a numeric string
- every xwoba is in [0.20, 0.40]
- no key equals the season number (the exact bug from the first attempt)
Aborts non-zero if any check fails so the bootstrap workflow's
validation step catches a bad pull.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import requests


BASE_URL = "https://statsapi.mlb.com/api/v1"

LEAGUE_FIP = 4.20
LEAGUE_XWOBA = 0.310
FIP_TO_XWOBA_SLOPE = 0.029
XWOBA_MIN = 0.250
XWOBA_MAX = 0.380

FIP_CONSTANT = 3.10

MIN_QUALIFIED_PITCHERS = 50
PITCHER_PA_FLOOR = 100  # matches MIN_XSTATS_PA in splits_loader


def _ip_to_float(ip_str) -> float:
    if ip_str is None or ip_str == "":
        return 0.0
    if isinstance(ip_str, (int, float)):
        return float(ip_str)
    try:
        whole, _, frac = str(ip_str).partition(".")
        thirds = {"": 0, "0": 0, "1": 1 / 3, "2": 2 / 3}.get(frac, 0)
        return float(whole) + thirds
    except (TypeError, ValueError):
        return 0.0


def compute_fip(hr, bb, hbp, k, ip) -> float | None:
    """Standard FIP = (13*HR + 3*(BB+HBP) - 2*K) / IP + cFIP."""
    if any(x is None for x in (hr, bb, hbp, k)):
        return None
    if ip is None or ip < 1:
        return None
    return (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONSTANT


def fip_to_xwoba(fip: float) -> float:
    """Linear FIP -> xwOBA proxy bounded to a plausible range."""
    proxy = LEAGUE_XWOBA + (fip - LEAGUE_FIP) * FIP_TO_XWOBA_SLOPE
    return max(XWOBA_MIN, min(XWOBA_MAX, round(proxy, 4)))


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def fetch_pitching_leaders(season: int, http_get=None) -> list[dict]:
    """Pull every QUALIFIED pitcher's season pitching stats.

    http_get is injectable for testing — defaults to requests.get.
    """
    if http_get is None:
        http_get = requests.get
    out: list[dict] = []
    offset = 0
    page = 250
    while True:
        url = (
            f"{BASE_URL}/stats?stats=season&season={season}&group=pitching"
            f"&playerPool=Q&limit={page}&offset={offset}"
        )
        resp = http_get(url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        try:
            splits = payload["stats"][0]["splits"]
        except (KeyError, IndexError):
            break
        if not splits:
            break
        for s in splits:
            stat = s.get("stat") or {}
            player = s.get("player") or {}
            out.append({
                "id": player.get("id"),
                "name": player.get("fullName"),
                "ip": _ip_to_float(stat.get("inningsPitched")),
                "era": _safe_float(stat.get("era")),
                "whip": _safe_float(stat.get("whip")),
                "hr": _safe_int(stat.get("homeRuns")),
                "bb": _safe_int(stat.get("baseOnBalls")),
                "hbp": _safe_int(stat.get("hitByPitch")),
                "k": _safe_int(stat.get("strikeOuts")),
                "bf": _safe_int(stat.get("battersFaced")),
            })
        if len(splits) < page:
            break
        offset += page
    return out


def build_pitching_xstats(rows: list[dict]) -> dict[str, dict]:
    """Translate pitching rows to {player_id: {pa, xwoba, xba, xslg}}."""
    out: dict[str, dict] = {}
    for r in rows:
        pid = r.get("id")
        if pid is None:
            continue
        bf = r.get("bf") or 0
        fip = compute_fip(r.get("hr"), r.get("bb"), r.get("hbp"),
                          r.get("k"), r.get("ip"))
        if fip is None:
            continue
        xwoba = fip_to_xwoba(fip)
        xba = round(0.250 + (xwoba - LEAGUE_XWOBA) * 0.7, 4)
        xslg = round(0.400 + (xwoba - LEAGUE_XWOBA) * 1.0, 4)
        out[str(pid)] = {
            "pa": bf,
            "xwoba": xwoba,
            "xba": xba,
            "xslg": xslg,
            "source": "fip_proxy",
        }
    return out


def validate_pitching(payload: dict, season: int) -> list[str]:
    """Return a list of validation errors (empty list = pass)."""
    errors: list[str] = []
    pitching = payload.get("pitching") or {}
    if not isinstance(pitching, dict):
        errors.append(f"'pitching' is not a dict: {type(pitching).__name__}")
        return errors
    if len(pitching) < MIN_QUALIFIED_PITCHERS:
        errors.append(
            f"only {len(pitching)} pitchers (expected >= {MIN_QUALIFIED_PITCHERS}) — "
            f"likely a partial / failed fetch."
        )
    for key, row in pitching.items():
        if not key.isdigit():
            errors.append(f"non-numeric key {key!r} in pitching dict")
            break
        if key == str(season):
            errors.append(
                f"player_id == season ({season}) — this was the original "
                f"Savant-CSV bug; refusing to write."
            )
            break
        xw = row.get("xwoba")
        if xw is None or not (0.20 <= xw <= 0.40):
            errors.append(
                f"player {key} has implausible xwoba={xw} (expect 0.20–0.40)"
            )
            break
    return errors


def bootstrap_season(season: int, out_dir: Path, http_get=None) -> dict:
    print(f"Fetching MLB Stats API pitching leaders for {season}...")
    rows = fetch_pitching_leaders(season, http_get=http_get)
    print(f"  {len(rows)} qualified pitcher rows returned")

    pitching = build_pitching_xstats(rows)
    print(f"  {len(pitching)} pitcher xwOBA proxies computed")

    payload = {
        "season": season,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": "fip_proxy:statsapi.mlb.com",
        "pitching": pitching,
        "batting": {},
    }

    errors = validate_pitching(payload, season)
    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(2)

    season_dir = out_dir / str(season)
    season_dir.mkdir(parents=True, exist_ok=True)
    target = season_dir / "statcast_xstats.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))
    size_kb = target.stat().st_size / 1024
    print(f"  wrote {target} ({size_kb:.1f} KB)")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bootstrap_mlb_xstats")
    default_season = datetime.utcnow().year - 1
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--season", type=int, default=None)
    grp.add_argument("--seasons", type=int, nargs="+", default=None)
    p.add_argument("--out", type=Path, default=Path("data/backfill/mlb"))
    p.add_argument("--default-season", type=int, default=default_season)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.seasons:
        seasons = args.seasons
    elif args.season is not None:
        seasons = [args.season]
    else:
        seasons = [args.default_season]

    print(f"Bootstrap target seasons: {seasons}")
    print(f"Output dir: {args.out}")
    for season in seasons:
        bootstrap_season(season, args.out)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
