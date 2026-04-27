"""Map a 0-100 NRFI probability to the project's red→green gradient.

Anchor points (matching the user-spec):
    0-30   → deep red       (#b00020)  STRONG YRFI
    31-45  → orange         (#ef6c00)  LEAN YRFI
    46-54  → yellow         (#fbc02d)  COIN FLIP / NO BET
    55-69  → light green    (#7cb342)  LEAN NRFI
    70-100 → deep green     (#1b5e20)  STRONG NRFI
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorBand:
    label: str
    hex_color: str
    rgb: tuple[int, int, int]
    signal: str


_BANDS: tuple[tuple[float, float, ColorBand], ...] = (
    (0.0,  30.0, ColorBand("Deep Red",   "#b00020", (176,   0,  32), "STRONG_YRFI")),
    (30.0, 45.0, ColorBand("Orange",     "#ef6c00", (239, 108,   0), "LEAN_YRFI")),
    (45.0, 55.0, ColorBand("Yellow",     "#fbc02d", (251, 192,  45), "COIN_FLIP")),
    (55.0, 70.0, ColorBand("Light Green","#7cb342", (124, 179,  66), "LEAN_NRFI")),
    (70.0, 100.01, ColorBand("Deep Green","#1b5e20", ( 27,  94,  32), "STRONG_NRFI")),
)


def nrfi_band(probability_pct: float) -> ColorBand:
    """Return the discrete band for a given NRFI percentage (0-100)."""
    p = max(0.0, min(100.0, float(probability_pct)))
    for lo, hi, band in _BANDS:
        if lo <= p < hi:
            return band
    return _BANDS[-1][2]


def gradient_hex(probability_pct: float) -> str:
    """Smooth red→yellow→green hex interpolation for charts/badges.

    Uses a piecewise linear blend through the band anchors so that the
    output is continuous (no abrupt jumps between buckets in heatmaps).
    """
    p = max(0.0, min(100.0, float(probability_pct))) / 100.0
    # Anchors at 0, 0.5, 1.0
    if p <= 0.5:
        t = p / 0.5
        r = int(176 + (251 - 176) * t)
        g = int(  0 + (192 -   0) * t)
        b = int( 32 + ( 45 -  32) * t)
    else:
        t = (p - 0.5) / 0.5
        r = int(251 + ( 27 - 251) * t)
        g = int(192 + ( 94 - 192) * t)
        b = int( 45 + ( 32 -  45) * t)
    return f"#{r:02x}{g:02x}{b:02x}"
