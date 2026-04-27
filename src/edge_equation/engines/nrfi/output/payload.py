"""Canonical output payload for the elite NRFI/YRFI engine.

One dataclass — `NRFIOutput` — that every consumer (dashboard, email,
API, posting card, X tweet renderer) reads. Three adapters off the
same payload so we never drift between channels:

* `to_email_card(out)`     — multi-line plain-text section.
* `to_api_dict(out)`       — JSON-serialisable dict for FastAPI.
* `to_dashboard_row(out)`  — Streamlit-friendly row including hex
                             colours and pre-formatted strings.

Construct via `build_output(prediction, feature_dict, ...)` rather
than instantiating directly — the factory wires the color band, the
Kelly stake, the SHAP top-N, and the MC band consistently.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ..utils.colors import gradient_hex, nrfi_band
from edge_equation.utils.kelly import implied_probability, kelly_stake


@dataclass(frozen=True)
class NRFIOutput:
    """The single canonical NRFI/YRFI payload."""

    # Identity
    game_id: str
    market_type: str = "NRFI"        # "NRFI" or "YRFI"

    # Probability
    nrfi_prob: float = 0.55          # 0..1
    nrfi_pct: float = 55.0           # 0..100, 1dp
    lambda_total: float = 1.20       # expected first-inning runs

    # Color
    color_band: str = "Light Green"
    color_hex: str = "#7cb342"
    signal: str = "LEAN_NRFI"

    # Confidence
    mc_low: Optional[float] = None
    mc_high: Optional[float] = None
    mc_band_pp: Optional[float] = None  # +/- pp display value

    # Drivers
    shap_drivers: list[tuple[str, float]] = field(default_factory=list)
    driver_text: list[str] = field(default_factory=list)  # human-readable

    # Market & stake
    market_prob: Optional[float] = None
    edge_pp: Optional[float] = None      # signed pp vs market
    kelly_units: Optional[float] = None  # 0 if no bet

    # Audit trail
    grade: str = "C"
    realization: int = 47
    engine: str = "ml"               # "ml" | "poisson_baseline"
    model_version: str = "elite_nrfi_v1"

    # ----------------------------------------------------------------------
    def headline(self) -> str:
        """One-liner: `78.4% NRFI` / `34.1% YRFI`."""
        return f"{self.nrfi_pct:.1f}% {self.market_type}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_output(
    *,
    game_id: str,
    blended_p: float,
    lambda_total: float,
    market_type: str = "NRFI",
    feature_dict: Optional[Mapping[str, float]] = None,
    shap_drivers: Optional[Sequence[tuple[str, float]]] = None,
    mc_low: Optional[float] = None,
    mc_high: Optional[float] = None,
    market_american_odds: Optional[float] = None,
    market_prob: Optional[float] = None,
    grade: str = "C",
    realization: int = 47,
    engine: str = "ml",
    model_version: str = "elite_nrfi_v1",
    # Stake config (let caller override per-config)
    kelly_fraction: float = 0.25,
    min_edge: float = 0.04,
    vig_buffer: float = 0.02,
    max_stake_units: float = 2.0,
) -> NRFIOutput:
    """Build a canonical NRFI/YRFI output payload from raw model inputs.

    All Kelly logic lives here so every consumer applies the same
    edge/vig/stake rules — no surprise discrepancies between the
    dashboard and the email body.
    """
    p = max(0.0, min(1.0, float(blended_p)))
    if market_type == "YRFI":
        p_for_side = 1.0 - p
    else:
        p_for_side = p

    band = nrfi_band(p_for_side * 100.0)
    hex_color = gradient_hex(p_for_side * 100.0)

    # Confidence band display — symmetric pp around the point estimate.
    mc_band_pp: Optional[float] = None
    if mc_low is not None and mc_high is not None:
        side_low = mc_low if market_type == "NRFI" else (1.0 - mc_high)
        side_high = mc_high if market_type == "NRFI" else (1.0 - mc_low)
        mc_band_pp = round((side_high - side_low) / 2.0 * 100.0, 1)

    # Top-N SHAP drivers, formatted with signed delta in NRFI%-points.
    drivers = list(shap_drivers or [])
    driver_text: list[str] = []
    for name, val in drivers[:5]:
        # SHAP values are in log-odds; convert to a rough NRFI%-point delta.
        # log-odds Δ ≈ Δp / (p*(1-p)). We linearise around p_for_side.
        denom = max(1e-3, p_for_side * (1.0 - p_for_side))
        delta_pct = float(val) / denom * 100.0
        # Flip sign for YRFI side so "+" always means "favors this market".
        if market_type == "YRFI":
            delta_pct = -delta_pct
        sign = "+" if delta_pct >= 0 else "−"
        driver_text.append(f"{sign}{abs(delta_pct):.1f} {name}")

    # Stake recommendation. Only computed when we have a market.
    edge_pp: Optional[float] = None
    kelly_units: Optional[float] = None
    if market_prob is None and market_american_odds is not None:
        market_prob = implied_probability(float(market_american_odds))
    if market_prob is not None:
        odds = market_american_odds if market_american_odds is not None else -110.0
        rec = kelly_stake(
            model_prob=p_for_side,
            market_prob=float(market_prob) if market_type == "NRFI" else (1.0 - float(market_prob)),
            american_odds=float(odds),
            fraction=kelly_fraction,
            min_edge=min_edge,
            vig_buffer=vig_buffer,
            max_stake_units=max_stake_units,
        )
        edge_pp = round(rec.edge * 100.0, 2)
        kelly_units = round(rec.stake_units, 2)

    return NRFIOutput(
        game_id=str(game_id),
        market_type=market_type,
        nrfi_prob=p_for_side,
        nrfi_pct=round(p_for_side * 100.0, 1),
        lambda_total=float(lambda_total),
        color_band=band.label,
        color_hex=hex_color,
        signal=band.signal,
        mc_low=mc_low, mc_high=mc_high, mc_band_pp=mc_band_pp,
        shap_drivers=drivers, driver_text=driver_text,
        market_prob=float(market_prob) if market_prob is not None else None,
        edge_pp=edge_pp,
        kelly_units=kelly_units,
        grade=grade,
        realization=int(realization),
        engine=engine,
        model_version=model_version,
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def to_api_dict(out: NRFIOutput) -> dict[str, Any]:
    """JSON-serialisable dict suitable for /nrfi/today response rows.

    Keeps shap_drivers as a list of [name, signed_value] pairs so JS
    consumers can render the bars without re-parsing.
    """
    d = asdict(out)
    d["shap_drivers"] = [[n, float(v)] for n, v in out.shap_drivers]
    d["headline"] = out.headline()
    return d


def to_email_card(out: NRFIOutput) -> str:
    """Plain-text section for the premium daily email.

    Matches the existing 'Facts. Not Feelings.' brand rules: no
    emojis, no hashtags in body, single-line summary plus drivers.
    """
    lines = [
        f"{out.headline():<14} [{out.color_band:>11}]  λ={out.lambda_total:.2f}  game={out.game_id}",
    ]
    if out.mc_band_pp is not None:
        lines[0] += f"  MC ±{out.mc_band_pp:.1f}pp"
    if out.edge_pp is not None:
        sign = "+" if out.edge_pp >= 0 else ""
        lines[0] += f"  edge {sign}{out.edge_pp:.1f}pp"
    if out.kelly_units is not None and out.kelly_units > 0:
        lines[0] += f"  stake {out.kelly_units:.2f}u"
    if out.driver_text:
        lines.append("  drivers: " + ", ".join(out.driver_text[:4]))
    return "\n".join(lines)


def to_dashboard_row(out: NRFIOutput) -> dict[str, Any]:
    """Streamlit-friendly row.

    Pre-formats every column the dashboard renders so the front-end
    doesn't need to know about Kelly / vig / SHAP encoding.
    """
    return {
        "Game": out.game_id,
        "NRFI %": f"{out.nrfi_pct:.1f}%",
        "Band": out.color_band,
        "Color": out.color_hex,
        "λ": f"{out.lambda_total:.2f}",
        "MC ±": f"±{out.mc_band_pp:.1f}pp" if out.mc_band_pp is not None else "—",
        "Edge": f"{out.edge_pp:+.1f}pp" if out.edge_pp is not None else "—",
        "Stake": f"{out.kelly_units:.2f}u" if (out.kelly_units or 0) > 0 else "—",
        "Drivers": " · ".join(out.driver_text[:3]),
        "Grade": out.grade,
        "Engine": out.engine,
    }


def to_jsonl(outputs: Sequence[NRFIOutput]) -> str:
    """Serialise a slate's worth of outputs to JSONL (one per line)."""
    return "\n".join(json.dumps(to_api_dict(o), default=str) for o in outputs)
