"""
NRFI engine bridge.

Wires v1's elite NRFI/YRFI engine (engines.nrfi, XGBoost + LightGBM +
SHAP + Monte Carlo) into the daily orchestrator's first_inning tab.
The orchestrator's projections.py computes nrfi_prob via a simple
league-rate Poisson; this bridge replaces those values with the
calibrated ML engine's outputs whenever the NRFI runtime is ready
(DuckDB store present, daily ETL fresh, model artifacts loaded).

Design intent
-------------
- **Optional, additive.** If the NRFI engine isn't available (missing
  DuckDB, missing model bridge, exception during predict), the bridge
  returns None and the orchestrator keeps the projector's in-house
  Poisson values. No regression.
- **Single hand-off.** apply_overrides(projections, target_date)
  mutates each projection's nrfi_prob / yrfi_prob in place. The
  downstream first_inning tab assembly logic at
  daily_spreadsheet._build_first_inning is untouched.
- **Feature-flag gated.** Even with NRFI runtime ready, the bridge
  only fires when EDGE_FEATURE_NRFI_BRIDGE=on (env). Off by default
  during the polish phase so we observe the verbatim port's behavior
  before swapping in the heavier engine.

How the override works
----------------------
For each projection {away_team, home_team, ..., nrfi_prob, yrfi_prob}
the bridge looks for a matching NRFI engine pick in build_card()'s
output (matched on home_team + away_team for the given date). When
found, it overwrites nrfi_prob with the engine's NRFI percentage
(scaled to a 0-1 fraction) and recomputes yrfi_prob = 1 - nrfi_prob.
The matchup keying must tolerate scrapers' team-code conventions
(e.g. "AZ" vs "ARI"); the helper canonicalizes both sides.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

log = logging.getLogger(__name__)


FEATURE_FLAG_ENV = "EDGE_FEATURE_NRFI_BRIDGE"


# Team-code aliases the NRFI engine and the scrapers' game scraper
# might disagree on. Canonicalize both sides to a single form before
# matching projections to NRFI picks.
TEAM_ALIASES = {
    "ARI": "AZ", "AZ": "AZ",
    "OAK": "ATH", "ATH": "ATH",
    "WAS": "WSH", "WSH": "WSH",
    "CHW": "CWS", "CWS": "CWS",
    "SFG": "SF", "SF": "SF",
    "SDP": "SD", "SD": "SD",
    "TBR": "TB", "TB": "TB",
    "KCR": "KC", "KC": "KC",
}


def _canon(team: str | None) -> str | None:
    if team is None:
        return None
    return TEAM_ALIASES.get(team.upper(), team.upper())


def _flag_value() -> str:
    """Return the flag's normalized value: 'on' / 'shadow' / 'off'.
    'shadow' = run the bridge but log delta only, don't override
    projections. Lets us measure NRFI engine vs projector on real picks
    before flipping fully on."""
    raw = os.environ.get(FEATURE_FLAG_ENV, "").strip().lower()
    if raw in ("1", "on", "true", "yes"):
        return "on"
    if raw == "shadow":
        return "shadow"
    return "off"


def _flag_on() -> bool:
    return _flag_value() == "on"


def _try_build_nrfi_card(target_date: str) -> dict | None:
    """Best-effort call into the NRFI engine. Returns None on any
    exception (missing DuckDB, missing model artifacts, ETL failure).
    Never propagates errors — the orchestrator must keep running with
    the projector's fallback in-house first-inning math.
    """
    try:
        from edge_equation.engines.nrfi.email_report import build_card
    except Exception as e:
        log.info("NRFI engine import failed (%s) — bridge inactive", e)
        return None
    try:
        # run_etl=True so the engine has fresh schedule + features.
        # build_card already gracefully handles ETL failure (logs warning,
        # proceeds with cached games).
        return build_card(target_date, run_etl=True)
    except Exception as e:
        log.warning("NRFI build_card failed (%s) — bridge falling back to projector", e)
        return None


def _index_picks_by_matchup(card: dict) -> dict[tuple[str, str], dict]:
    """Index NRFI picks by (away, home) canonical team codes so the
    orchestrator's projections can look up overrides in O(1).

    Each NRFI pick has at minimum: market_type ("NRFI" or "YRFI"),
    pct (a probability in percent units, 0-100), and a matchup label
    like "AZ @ CHC" or analogous. We use whichever of (game_pk,
    matchup, away_team, home_team) the engine populates.
    """
    out: dict[tuple[str, str], dict] = {}
    for pick in card.get("picks", []) or []:
        away = _canon(pick.get("away_team") or pick.get("away"))
        home = _canon(pick.get("home_team") or pick.get("home"))
        if not (away and home):
            label = pick.get("matchup") or ""
            if "@" in label:
                a, h = (s.strip() for s in label.split("@", 1))
                away = away or _canon(a)
                home = home or _canon(h)
        if not (away and home):
            continue
        # NRFI picks come in pairs (NRFI side + YRFI side per game). We
        # only need one — keep whichever has higher pct so the override
        # uses the engine's preferred side. nrfi_prob = NRFI%/100 by
        # convention; if the higher-pct pick is the YRFI side, flip.
        market = (pick.get("market_type") or "").upper()
        pct = float(pick.get("pct", 0) or 0)
        nrfi_prob = pct / 100.0 if market == "NRFI" else 1.0 - (pct / 100.0)
        existing = out.get((away, home))
        if existing is None or abs(nrfi_prob - 0.5) > abs(existing["nrfi_prob"] - 0.5):
            out[(away, home)] = {"nrfi_prob": nrfi_prob, "engine_pick": pick}
    return out


def apply_overrides(
    projections: Iterable[dict],
    target_date: str,
) -> dict:
    """Mutate each projection's nrfi_prob/yrfi_prob in place using the
    NRFI engine's calibrated ML probabilities, when available.

    Returns a small report dict describing the bridge's activity:
        {"active": bool, "applied": int, "skipped": int, "engine_label": str}

    Reasons "active" might be False:
        - feature flag off
        - NRFI engine import failed (missing dependency / not installed)
        - build_card raised
        - card had zero picks

    Reasons a projection might be in "skipped":
        - no engine pick matched its (away, home) tuple after canonicalization
    """
    mode = _flag_value()
    if mode == "off":
        return {"active": False, "applied": 0, "skipped": 0, "reason": "flag_off"}

    card = _try_build_nrfi_card(target_date)
    if card is None or not card.get("picks"):
        return {
            "active": False, "applied": 0, "skipped": 0,
            "reason": "engine_unavailable",
        }

    index = _index_picks_by_matchup(card)
    applied = 0
    skipped = 0
    deltas: list[dict] = []  # populated in shadow mode for measurement
    for proj in projections:
        away = _canon(proj.get("away_team"))
        home = _canon(proj.get("home_team"))
        match = index.get((away, home))
        if match is None:
            skipped += 1
            continue
        engine_nrfi = round(float(match["nrfi_prob"]), 3)
        projector_nrfi = float(proj.get("nrfi_prob") or 0.5)
        if mode == "on":
            proj["nrfi_prob"] = engine_nrfi
            proj["yrfi_prob"] = round(1.0 - proj["nrfi_prob"], 3)
            proj["nrfi_pick"] = "NRFI" if proj["nrfi_prob"] >= 0.5 else "YRFI"
            applied += 1
        else:  # shadow — log only, don't mutate
            deltas.append({
                "matchup": f"{away}@{home}",
                "projector_nrfi": round(projector_nrfi, 3),
                "engine_nrfi": engine_nrfi,
                "delta": round(engine_nrfi - projector_nrfi, 3),
            })
            applied += 1

    engine_label = card.get("engine_label") or "ml-bridge"
    return {
        "active": applied > 0,
        "applied": applied,
        "skipped": skipped,
        "engine_label": engine_label,
        "mode": mode,
        # Empty list in 'on' mode; populated in shadow mode so the
        # orchestrator can log per-game side-by-side without touching
        # the actual projection output.
        "shadow_deltas": deltas,
    }
