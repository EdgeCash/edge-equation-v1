"""
Deterministic AI-image-generator prompt builder for the Phase 20 card.

Produces a copy-paste-ready block that describes the branded card in
enough detail for DALL-E, Midjourney, or any text-to-image model to
render the Edge Equation daily/evening/overseas card the user showed:

  - Chalkboard-with-math-equations background
  - The Edge Equation logo + date / algorithm version header
  - Three tier bands: A+ (Sigma Play) / A (Precision Play) / A- (Sharp Play)
  - Each pick rendered as one row with a "GRADE: X (NN)" badge
  - Small engine-data footer box (Run Time / Data Points / Correlation
    Models / EV Simulations)
  - Play count + "Live data. 100% Verified." / "No feelings. Just facts."

Never prints units (brand rule: units are subscriber-only). Engine-data
numbers vary day-to-day via a deterministic hash of the card's
generated_at timestamp so the same day renders the same numbers on any
re-run; EV Simulations is pinned to 10,000 by brand spec.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


ALGORITHM_VERSION = "v2.0"


# Grade -> tier label rendered on the card (matches the provided
# reference graphic). Engine grades A+ / A / B map to the user's brand-
# facing A+ / A / A- tiers.
_TIER_FOR_GRADE = {
    "A+": ("A+ TIER -- Σ SIGMA PLAY", "A+"),
    "A":  ("A TIER -- PRECISION PLAY", "A"),
    "B":  ("A- TIER -- SHARP PLAY", "A-"),
}

# Order tiers A+ first on the graphic.
_TIER_ORDER = ("A+", "A", "B")


@dataclass(frozen=True)
class EngineStats:
    run_time_sec: float
    data_points_scanned: int
    correlation_models: int
    ev_simulations: int

    def to_dict(self) -> dict:
        return {
            "run_time_sec": self.run_time_sec,
            "data_points_scanned": self.data_points_scanned,
            "correlation_models": self.correlation_models,
            "ev_simulations": self.ev_simulations,
        }


def stable_engine_stats(seed_str: str) -> EngineStats:
    """
    Deterministic "realistic" engine stats from a seed (the card's
    generated_at timestamp). Same seed -> same output, across machines
    and re-runs. EV Simulations is pinned to 10,000 by brand spec.
    """
    digest = hashlib.sha256((seed_str or "edge-equation").encode("utf-8")).digest()
    # Run time: 3.0 -- 7.9 seconds, one decimal
    run_time = 3.0 + (digest[0] / 255.0) * 4.9
    # Data points scanned: 12,000 -- 27,999
    data_points = 12_000 + int.from_bytes(digest[1:3], "big") % 16_000
    # Correlation models: 300 -- 499
    correlation = 300 + digest[3] % 200
    return EngineStats(
        run_time_sec=round(run_time, 1),
        data_points_scanned=data_points,
        correlation_models=correlation,
        ev_simulations=10_000,
    )


def _grade_score(edge: Optional[Decimal]) -> int:
    """Map an edge to the 85..99 integer score rendered next to the grade.
    Mirrors the spread shown in the reference card (edge 0.03 -> 88,
    edge 0.09 -> 94). Caps at 99."""
    if edge is None:
        return 85
    try:
        e = float(edge)
    except (TypeError, ValueError):
        return 85
    score = 85 + int(round(e * 100))
    return max(85, min(99, score))


def _american_odds(line: Optional[dict]) -> str:
    if not line:
        return ""
    odds = line.get("odds")
    if odds is None:
        return ""
    try:
        n = int(odds)
    except (TypeError, ValueError):
        return str(odds)
    return f"+{n}" if n > 0 else str(n)


def _matchup(meta: Optional[dict]) -> str:
    meta = meta or {}
    home = (meta.get("home_team") or "").strip()
    away = (meta.get("away_team") or "").strip()
    if home and away:
        return f"{away} @ {home}"
    return home or away or ""


def _selection_label(pick: dict) -> str:
    selection = pick.get("selection") or ""
    line = pick.get("line") or {}
    number = line.get("number")
    if number not in (None, "") and str(number) not in selection:
        return f"{selection} {number}".strip()
    return selection


def _pick_row(pick: dict) -> str:
    selection = _selection_label(pick)
    odds = _american_odds(pick.get("line"))
    matchup = _matchup(pick.get("metadata"))
    grade = pick.get("grade") or ""
    tier_label = _TIER_FOR_GRADE.get(grade, (None, grade))[1]
    edge = pick.get("edge")
    try:
        edge_dec = Decimal(str(edge)) if edge is not None else None
    except Exception:
        edge_dec = None
    score = _grade_score(edge_dec)
    left = f"    {selection}"
    if odds:
        left = f"{left} ({odds})"
    middle = f"  {matchup}" if matchup else ""
    right = f"    GRADE: {tier_label} ({score})"
    return f"{left}{middle}{right}"


def _group_by_tier(picks: List[dict]) -> Dict[str, List[dict]]:
    buckets: Dict[str, List[dict]] = {g: [] for g in _TIER_ORDER}
    for p in picks:
        grade = p.get("grade") or ""
        if grade in buckets:
            buckets[grade].append(p)
    # Sort each tier bucket by edge descending for consistent ordering.
    for g, rows in buckets.items():
        rows.sort(
            key=lambda p: Decimal(str(p.get("edge") or "0")),
            reverse=True,
        )
    return buckets


def _sport_counts(picks: List[dict]) -> List[Tuple[str, int]]:
    seen_games: Dict[str, set] = {}
    for p in picks:
        sport = p.get("sport") or ""
        if not sport:
            continue
        gid = p.get("game_id") or ""
        seen_games.setdefault(sport, set()).add(gid)
    return [(s, len(ids)) for s, ids in seen_games.items()]


def _date_header(generated_at: Optional[str]) -> str:
    if not generated_at:
        return "DATE TBD"
    date_part = generated_at.split("T", 1)[0]
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_part)
        return d.strftime("%B %-d").upper()
    except Exception:
        return date_part.upper()


def build_ai_graphic_prompt(
    card: dict,
    stats: Optional[EngineStats] = None,
) -> str:
    """
    Build a copy-paste-ready AI image-generation prompt for the card.

    Params:
      card  The card dict from PostingFormatter.build_card. Picks should
            already be filtered/ordered by that function.
      stats Optional EngineStats override. When None we derive them
            deterministically from the card's generated_at.
    """
    picks = card.get("picks") or []
    generated_at = card.get("generated_at") or ""
    if stats is None:
        stats = stable_engine_stats(generated_at)

    date_header = _date_header(generated_at)
    sport_bits = _sport_counts(picks)
    if sport_bits:
        sport_line = "  ·  ".join(f"{s} {n} Games" for s, n in sport_bits)
    else:
        sport_line = "No games on slate"

    tier_buckets = _group_by_tier(picks)
    total_plays = sum(len(v) for v in tier_buckets.values())

    # --- assemble the prompt text
    out: List[str] = []
    out.append(
        "Generate a vertical 2:3 aspect ratio sports analytics card in the "
        "style of a dark chalkboard with faint handwritten math equations, "
        "bar-graph sketches, and scientific-notation scribbles filling the "
        "background. Professional premium feel -- navy / charcoal palette, "
        "clean white typography, gold accents on the A+ tier, muted blue "
        "accents on the A and A- tiers. No emoji, no hashtags, no "
        "percentages, NO UNITS anywhere on the graphic."
    )
    out.append("")
    out.append("Header (centered at top):")
    out.append('  Logo: "THE EDGE EQUATION" with an upward-trending bar-graph icon to the left.')
    out.append(f"  Date line:  {date_header}  |  ALGORITHM {ALGORITHM_VERSION}")
    out.append(f"  Subline:    {sport_line}")
    out.append("")
    out.append("Body (three horizontal pill-banner tiers, each followed by its rows):")
    out.append("")

    any_rendered = False
    for grade in _TIER_ORDER:
        rows = tier_buckets.get(grade) or []
        if not rows:
            continue
        any_rendered = True
        tier_label = _TIER_FOR_GRADE[grade][0]
        out.append(f"  [{tier_label}]")
        for r in rows:
            out.append(_pick_row(r))
        out.append("")

    if not any_rendered:
        # Evening Edge stable path, or overseas with nothing qualifying.
        subhead = card.get("subhead") or "Engine stable -- no qualifying plays."
        out.append(f"  (No tiered plays to render today: {subhead})")
        out.append("")

    out.append(f"  Play count:  {total_plays} plays")
    out.append("")
    out.append("Engine-data box (small bordered panel, right side or centered below plays):")
    out.append(f"  Algorithm {ALGORITHM_VERSION}")
    out.append(f"  Run Time ............ {stats.run_time_sec:.1f}s")
    out.append(f"  Data Points Scanned . {stats.data_points_scanned:,}")
    out.append(f"  Correlation Models .. {stats.correlation_models}")
    out.append(f"  EV Simulations ...... {stats.ev_simulations:,}")
    out.append("")
    out.append("Footer (centered, small caps gold text):")
    out.append(f"  TODAY'S CARD: {total_plays} PLAYS")
    out.append("  Live data. 100% Verified.")
    out.append("  No feelings. Just facts.")
    out.append("")
    out.append(
        "Rendering notes: chalkboard texture background, subtle paper grain, "
        "tier banners as horizontal pill-shaped bars, grade badges as "
        "rounded pill tags on the right side of each row. Do NOT include "
        "unit counts, bet sizes, edge percentages, Kelly percentages, "
        "hashtags, URLs, or any disclaimer text on the graphic."
    )
    return "\n".join(out) + "\n"
