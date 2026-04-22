"""
Major Variance Signal (MVS).

A rare, high-credibility premium-only signal. Fires when all four
conditions hold simultaneously:

  1. Grade == "A+"                 (strongest calibration bucket)
  2. edge >= 12.0%                 (materially above the A+ floor of 8%)
  3. kelly >= 5.0% of bankroll     (post-uncertainty-shrinkage allocation
                                    is large enough to matter)
  4. Monte Carlo stability is HIGH -- explicit stdev < 9% OR the
                                    10th-90th percentile band is tight
                                    (p90 - p10 < 12%).

CREDIBILITY-FIRST RULE: If ANY of the four data points is unavailable,
the signal does NOT fire. A half-baked trigger (e.g., "high grade but
we didn't measure stability") would erode the signal's meaning. Missing
data -> silence, not speculation.

Target frequency: 1-4 per WEEK across all sports. If your detector is
firing every day, the thresholds are wrong and the signal is no longer
rare. Tune the thresholds, never the evidence.

Premium-only: the detector tags a pick's metadata. Rendering is handled
in posting/premium_daily_body.py. public_mode (free X/Discord posts)
MUST strip the flag via PublicModeSanitizer before reaching a reader.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from edge_equation.engine.pick_schema import Pick


SIGNAL_LABEL = "Major Variance Signal"

# Strict thresholds. Credibility-first: never loosen these to chase
# eyeballs. If a pick is "almost A+" or has edge 11.9%, it is NOT a
# Major Variance Signal -- it's a strong A+ or a normal pick. The
# whole point is the signal fires ONLY when the model's own arithmetic
# says it's exceptionally well-grounded.
REQUIRED_GRADE = "A+"
MIN_EDGE = Decimal("0.12")           # 12.0% calibrated edge
MIN_KELLY = Decimal("0.05")          # 5.0% of bankroll, post-shrinkage
MAX_MC_STDEV = Decimal("0.09")       # std-dev of win probability across sims
MAX_MC_P10_P90_BAND = Decimal("0.12")  # fallback: tight 10th-90th band

META_KEY = "major_variance_signal"   # metadata flag on tagged Pick
META_REASON_KEY = "major_variance_reason"


@dataclass(frozen=True)
class MajorVarianceSignal:
    """Result of running the detector on one pick. When fires=True the
    pick's metadata was (or should be) tagged with META_KEY=True."""
    fires: bool
    grade: str
    edge: Optional[Decimal]
    kelly: Optional[Decimal]
    mc_stdev: Optional[Decimal]
    mc_p10_p90_band: Optional[Decimal]
    reason: str

    def to_dict(self) -> dict:
        return {
            "fires": self.fires,
            "label": SIGNAL_LABEL if self.fires else None,
            "grade": self.grade,
            "edge": str(self.edge) if self.edge is not None else None,
            "kelly": str(self.kelly) if self.kelly is not None else None,
            "mc_stdev": str(self.mc_stdev) if self.mc_stdev is not None else None,
            "mc_p10_p90_band": str(self.mc_p10_p90_band) if self.mc_p10_p90_band is not None else None,
            "reason": self.reason,
        }


def _extract_mc(mc_stability: Optional[Dict[str, Any]]) -> (Optional[Decimal], Optional[Decimal]):
    """Pull stdev and/or p10/p90 band out of a Monte Carlo result dict.

    Accepted shapes:
      {"stdev": 0.07}
      {"p10": 0.48, "p90": 0.58}
      {"stdev": 0.07, "p10": 0.48, "p90": 0.58}
    Any type-cast failure returns (None, None) so the detector falls
    through to "data unavailable -> no fire" (credibility-first).
    """
    if not mc_stability:
        return None, None
    try:
        stdev_raw = mc_stability.get("stdev")
        stdev = Decimal(str(stdev_raw)) if stdev_raw is not None else None
    except Exception:
        stdev = None
    try:
        p10 = mc_stability.get("p10")
        p90 = mc_stability.get("p90")
        if p10 is not None and p90 is not None:
            band = Decimal(str(p90)) - Decimal(str(p10))
            if band < Decimal("0"):
                band = None
        else:
            band = None
    except Exception:
        band = None
    return stdev, band


def detect(
    pick: Pick,
    mc_stability: Optional[Dict[str, Any]] = None,
) -> MajorVarianceSignal:
    """
    Check all four trigger conditions against a Pick. If mc_stability is
    None the detector looks for the same dict under pick.metadata["mc_stability"]
    so the engine can stash it once and every downstream call agrees.
    """
    grade = pick.grade or ""
    edge = pick.edge
    kelly = pick.kelly

    # Fold metadata MC if caller didn't pass one. Allows a single engine
    # pass to compute MC once and every subsequent detector call reuses it.
    if mc_stability is None:
        meta = pick.metadata or {}
        mc_stability = meta.get("mc_stability")
    mc_stdev, mc_band = _extract_mc(mc_stability)

    # --- Credibility-first gating. Missing evidence -> no fire. ---
    if grade != REQUIRED_GRADE:
        return MajorVarianceSignal(
            fires=False, grade=grade, edge=edge, kelly=kelly,
            mc_stdev=mc_stdev, mc_p10_p90_band=mc_band,
            reason=f"grade {grade!r} != {REQUIRED_GRADE!r}",
        )
    if edge is None or edge < MIN_EDGE:
        return MajorVarianceSignal(
            fires=False, grade=grade, edge=edge, kelly=kelly,
            mc_stdev=mc_stdev, mc_p10_p90_band=mc_band,
            reason=f"edge {edge} < {MIN_EDGE}",
        )
    if kelly is None or kelly < MIN_KELLY:
        return MajorVarianceSignal(
            fires=False, grade=grade, edge=edge, kelly=kelly,
            mc_stdev=mc_stdev, mc_p10_p90_band=mc_band,
            reason=f"kelly {kelly} < {MIN_KELLY}",
        )

    # MC stability. At least one of stdev/band must be present AND pass
    # its threshold. "Low stdev OR tight band" is an OR by design -- a
    # tight band with unknown stdev is still strong evidence.
    stability_ok = False
    if mc_stdev is not None and mc_stdev < MAX_MC_STDEV:
        stability_ok = True
    if mc_band is not None and mc_band < MAX_MC_P10_P90_BAND:
        stability_ok = True
    if not stability_ok:
        if mc_stdev is None and mc_band is None:
            reason = "mc_stability data unavailable (credibility-first: no fire)"
        else:
            reason = (
                f"mc stability below bar (stdev={mc_stdev}, band={mc_band})"
            )
        return MajorVarianceSignal(
            fires=False, grade=grade, edge=edge, kelly=kelly,
            mc_stdev=mc_stdev, mc_p10_p90_band=mc_band,
            reason=reason,
        )

    return MajorVarianceSignal(
        fires=True, grade=grade, edge=edge, kelly=kelly,
        mc_stdev=mc_stdev, mc_p10_p90_band=mc_band,
        reason="all four thresholds satisfied",
    )


def tag_pick(pick: Pick, signal: MajorVarianceSignal) -> Pick:
    """Return a copy of `pick` with metadata.major_variance_signal + the
    stability evidence stashed for downstream renderers. A non-firing
    signal still gets the reason recorded (for audit logs) but
    major_variance_signal stays False so renderers don't surface it.
    """
    new_meta = dict(pick.metadata or {})
    new_meta[META_KEY] = bool(signal.fires)
    new_meta[META_REASON_KEY] = signal.reason
    if signal.mc_stdev is not None:
        new_meta.setdefault("mc_stability", {})
        new_meta["mc_stability"]["stdev"] = str(signal.mc_stdev)
    if signal.mc_p10_p90_band is not None:
        new_meta.setdefault("mc_stability", {})
        new_meta["mc_stability"]["p10_p90_band"] = str(signal.mc_p10_p90_band)
    return Pick(
        sport=pick.sport, market_type=pick.market_type,
        selection=pick.selection, line=pick.line,
        fair_prob=pick.fair_prob, expected_value=pick.expected_value,
        edge=pick.edge, kelly=pick.kelly, grade=pick.grade,
        realization=pick.realization, game_id=pick.game_id,
        event_time=pick.event_time,
        decay_halflife_days=pick.decay_halflife_days,
        hfa_value=pick.hfa_value,
        kelly_breakdown=pick.kelly_breakdown,
        metadata=new_meta,
    )


def is_tagged(pick: Pick) -> bool:
    """True iff the pick has previously been tagged as a Major Variance
    Signal (metadata flag). Safe on untagged picks -- returns False."""
    meta = pick.metadata or {}
    return bool(meta.get(META_KEY))
