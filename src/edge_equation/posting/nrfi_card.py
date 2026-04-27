"""Renderer for the elite NRFI/YRFI card.

Drops a self-contained text section ready to be inserted into the
premium daily email body or X card. Style matches the existing
`PostingFormatter` conventions:

* Plain text only (no emojis, no hashtags in body).
* "Facts. Not Feelings." brand line.
* Each pick is a single-line summary plus one optional driver line.
* Color band rendered as a textual tag (DEEP GREEN / LIGHT GREEN / ...)
  rather than RGB — the email renderer can swap to inline CSS if it
  wants to use the hex code stored on the same dict.

The renderer is intentionally a pure function over a list of plain
dicts (the same shape the slate runner produces), so it can be unit
tested without spinning up the engine or any HTTP fixtures.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional


# Display thresholds so the card stays readable even on noisy slates.
DEFAULT_MIN_NRFI_PCT = 60.0   # only show NRFI picks at or above 60%
DEFAULT_MIN_YRFI_PCT = 60.0   # only show YRFI picks at or above 60% (i.e. NRFI<=40)
DEFAULT_MAX_PICKS = 6


def render_nrfi_section(
    picks: Iterable[Mapping],
    *,
    min_nrfi_pct: float = DEFAULT_MIN_NRFI_PCT,
    min_yrfi_pct: float = DEFAULT_MIN_YRFI_PCT,
    max_picks: int = DEFAULT_MAX_PICKS,
    show_drivers: bool = True,
) -> str:
    """Compose the NRFI section as a multi-line string.

    `picks` is an iterable of dicts with at minimum:

        {
            "game_id": str,
            "market_type": "NRFI" | "YRFI",
            "selection": "NRFI" | "YRFI",
            "nrfi_pct": float,           # already 0..100
            "color_band": str,
            "lambda_total": float,
            "edge": float | None,
            "kelly_units": float | None,
            "shap_drivers": list[(name, value)] | None,
        }

    Returns "" when no picks clear the threshold (caller should skip
    the section entirely in that case).
    """
    rows = list(picks)
    keep: list[dict] = []
    for r in rows:
        m = r.get("market_type", "")
        pct = float(r.get("nrfi_pct", 50.0))
        if m == "NRFI" and pct >= min_nrfi_pct:
            keep.append(dict(r))
        elif m == "YRFI" and pct >= min_yrfi_pct:
            keep.append(dict(r))
    if not keep:
        return ""

    keep.sort(key=lambda r: -float(r.get("nrfi_pct", 0.0)))
    keep = keep[:max_picks]

    lines = [
        "FIRST-INNING SIGNAL",
        "Facts. Not Feelings.",
        "",
    ]
    for r in keep:
        lines.append(_render_pick_line(r))
        if show_drivers:
            d = _render_driver_line(r)
            if d:
                lines.append(d)
        lines.append("")
    # Trim trailing blank.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _render_pick_line(r: Mapping) -> str:
    """One-line summary for a single NRFI/YRFI pick."""
    m = r.get("market_type", "?")
    pct = float(r.get("nrfi_pct", 0.0))
    band = (r.get("color_band") or "").upper().replace(" ", "_") or "NEUTRAL"
    lam = r.get("lambda_total")
    edge = r.get("edge")
    kelly = r.get("kelly_units")

    parts = [f"{m}", f"{pct:>4.1f}%", f"[{band}]"]
    if lam is not None:
        parts.append(f"λ={float(lam):.2f}")
    if edge is not None:
        parts.append(f"edge={float(edge) * 100.0:+.1f}pp")
    if kelly is not None and float(kelly) > 0:
        parts.append(f"stake={float(kelly):.2f}u")
    parts.append(str(r.get("game_id", "")))
    return "  ".join(parts)


def _render_driver_line(r: Mapping) -> Optional[str]:
    drivers = r.get("shap_drivers") or []
    if not drivers:
        return None
    # Top 3 drivers, sign-encoded.
    out = []
    for entry in drivers[:3]:
        try:
            name, val = entry
        except (TypeError, ValueError):
            continue
        arrow = "+" if float(val) >= 0 else "-"
        out.append(f"{arrow}{name}")
    if not out:
        return None
    return "  drivers: " + ", ".join(out)


def render_nrfi_dashboard_payload(picks: Iterable[Mapping]) -> dict:
    """Structured payload for the website / API / dashboard.

    Returns a dict with `picks` list (richer than the text card —
    keeps numeric values + hex colors so the front-end can render the
    gradient bar), plus a `meta` block with min/max/avg statistics.
    """
    rows = [dict(r) for r in picks if r.get("market_type") in ("NRFI", "YRFI")]
    if not rows:
        return {"picks": [], "meta": {"count": 0}}
    pcts = [float(r.get("nrfi_pct", 0.0)) for r in rows]
    return {
        "picks": rows,
        "meta": {
            "count": len(rows),
            "min_pct": min(pcts),
            "max_pct": max(pcts),
            "avg_pct": sum(pcts) / len(pcts),
        },
    }
