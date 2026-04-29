"""Daily NRFI/YRFI email report.

Runs the NRFI engine against today's slate and sends a standalone
email — separate from the premium Daily Edge — containing only
NRFI/YRFI predictions plus their drivers, MC band, and (when market
odds are present) Kelly stake recommendations.

Usage
-----

    # Dry run: build the card, print to stdout, do NOT send.
    python -m nrfi.email_report --dry-run

    # Live: build + send via SMTP using EMAIL_TO / SMTP_TO env vars.
    python -m nrfi.email_report

    # Custom date / recipient (still respects env precedence)
    python -m nrfi.email_report --date 2026-04-28 --to me@example.com

Configuration
-------------

Reads SMTP and recipient env vars from the same set the rest of the
Edge Equation publishing layer uses (see `.env.example` and
`src/edge_equation/publishing/email_publisher.py`):

    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    EMAIL_TO          (preferred recipient)
    SMTP_TO           (fallback recipient)

The cron workflow (`.github/workflows/nrfi-daily-email.yml`) defaults
the recipient to `ProfessorEdgeCash@gmail.com` matching the existing
premium-daily workflow.

Body shape
----------

Plain text only — same brand rules as `posting/nrfi_card.py`:

    Edge Equation — NRFI/YRFI Daily — 2026-04-28
    Facts. Not Feelings.

    FIRST-INNING SIGNAL                    (engine: ml or poisson_baseline)
    ---------------------------------------------------------------------
    NRFI 78.4% [Deep Green]  λ=0.49  MC ±5.2pp  edge +6.1pp  stake 1.20u
        drivers: +14 home_p_xera, +8 park_factor_runs, -5 wx_temperature_f
        game: MLB-2026-04-28-NYY-BOS  first pitch 19:05Z  Yankee Stadium

    YRFI 71.0% [Deep Green]  λ=2.40  MC ±4.8pp  edge +4.7pp  stake 0.94u
        drivers: ...
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict
from datetime import date as _date, datetime, timezone
from typing import Any, Iterable, Optional

from .config import get_default_config
from .data.scrapers_etl import daily_etl
from .data.storage import NRFIStore
from .evaluation.backtest import reconstruct_features_for_date
from .integration.engine_bridge import NRFIEngineBridge
from .output import build_output, to_email_card
from edge_equation.utils.logging import get_logger

log = get_logger(__name__, "INFO")

CARD_TYPE = "nrfi-daily"
SUBJECT_PREFIX = "Edge Equation — NRFI/YRFI Daily"


# ---------------------------------------------------------------------------
# Card construction
# ---------------------------------------------------------------------------

def build_card(target_date: str, *, run_etl: bool = True) -> dict:
    """Build the EmailPublisher-compatible card dict for `target_date`.

    Returns a dict with `card_type`, `headline`, `subhead`, `tagline`,
    `picks` (rich per-game NRFI/YRFI rows), and `generated_at` so the
    publisher's `build_subject` / `build_body` produce a clean email.

    `run_etl=False` skips the schedule pull (useful for tests or when
    the daily workflow already ran the ingestion job earlier).
    """
    cfg = get_default_config().resolve_paths()
    store = NRFIStore(cfg.duckdb_path)
    if run_etl:
        try:
            daily_etl(target_date, store, config=cfg)
        except Exception as e:
            log.warning("daily_etl failed (%s) — proceeding with cached games", e)

    # Build features for every game on the slate, then run the engine.
    # Live daily run → forecast weather (the archive endpoint lags ~5
    # days and rejects future-dated requests with a 400).
    feats_per_game = reconstruct_features_for_date(
        target_date, store=store, config=cfg,
        forecast_weather_only=True,
    )
    bridge = NRFIEngineBridge.try_load(cfg)
    engine_label = "ml" if bridge.available() else "poisson_baseline"

    # Build a {game_pk: "AWY @ HOM"} label map so we surface human-
    # readable matchups instead of bare gamePk integers in the email.
    label_map = _build_game_label_map(store, target_date)

    picks: list[dict] = []
    if feats_per_game:
        # Engine-bridge produces NRFI + YRFI rows per game
        outputs = bridge.predict_for_features(
            [f for _, f in feats_per_game],
            game_ids=[str(pk) for pk, _ in feats_per_game],
        )
        # Rebuild canonical NRFIOutput so we get all the email-friendly
        # fields (mc_band_pp, driver_text, etc.).
        for o in outputs:
            picks.append(_to_card_pick(o, engine_label, label_map))

    # Sort: NRFI side first (descending NRFI%), then YRFI side.
    picks.sort(key=lambda p: (
        0 if p["market_type"] == "NRFI" else 1,
        -float(p.get("pct", 0.0)),
    ))

    # Settle yesterday's slate before rendering today's so the YTD
    # ledger reflects last night's results. Best-effort — a network
    # blip on actuals shouldn't block the daily email.
    ledger_text = ""
    try:
        from .ledger import render_ledger_section, settle_predictions
        season = int(target_date[:4])
        settle_predictions(store, season=season, cutoff_date=target_date,
                            config=cfg, pull_actuals=True)
        ledger_text = render_ledger_section(store, season=season)
    except Exception as e:
        log.warning("ledger settlement skipped (%s): %s",
                     type(e).__name__, e)

    # Phase 6 integration: feed today's qualifying picks into the
    # parlay builder. Best-effort — failure here must never block the
    # daily email. Auto-record is intentionally OFF; the operator
    # records the 0-2 tickets they actually place via the CLI.
    parlay_text = ""
    parlay_ledger_text = ""
    try:
        parlay_text, parlay_ledger_text = _build_parlay_block(
            outputs if feats_per_game else [],
            label_map, store,
        )
    except Exception as e:
        log.warning("parlay block skipped (%s): %s",
                      type(e).__name__, e)

    # Props integration (Props-3): build today's props card and append
    # the polished top-N-by-edge block + props YTD ledger to the email
    # body. Best-effort — any failure (Odds API down, pybaseball not
    # installed, props DuckDB missing) returns empty strings and the
    # NRFI email proceeds untouched.
    props_top_text = ""
    props_ledger_text = ""
    try:
        from edge_equation.engines.props_prizepicks.daily import build_props_card
        props_card = build_props_card(target_date)
        props_top_text = props_card.top_board_text
        props_ledger_text = props_card.ledger_text
    except Exception as e:
        log.warning("props daily card skipped (%s): %s",
                      type(e).__name__, e)

    # Full-game integration (FG-3): build today's full-game card and
    # append the polished top-N-by-edge block + per-tier YTD ledger
    # below the props block. Best-effort — Odds API errors / missing
    # rates / DuckDB issues all degrade silently.
    fullgame_top_text = ""
    fullgame_ledger_text = ""
    try:
        from edge_equation.engines.full_game.daily import build_full_game_card
        fullgame_card = build_full_game_card(target_date)
        fullgame_top_text = fullgame_card.top_board_text
        fullgame_ledger_text = fullgame_card.ledger_text
    except Exception as e:
        log.warning("full-game daily card skipped (%s): %s",
                      type(e).__name__, e)

    subject_date = target_date
    return {
        "card_type": CARD_TYPE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": f"NRFI/YRFI Daily — {subject_date}",
        "subhead": "Facts. Not Feelings.",
        "tagline": _footer_text(engine_label),
        "engine": engine_label,
        "target_date": subject_date,
        "picks": picks,
        "ledger_text": ledger_text,
        "parlay_text": parlay_text,
        "parlay_ledger_text": parlay_ledger_text,
        "props_top_text": props_top_text,
        "props_ledger_text": props_ledger_text,
        "fullgame_top_text": fullgame_top_text,
        "fullgame_ledger_text": fullgame_ledger_text,
    }


def _to_card_pick(bridge_output, engine_label: str,
                   label_map: dict[str, str] | None = None) -> dict:
    """Map an `NRFIBridgeOutput` to the dict shape expected by
    `EmailPublisher.build_body` + our richer custom renderer.

    Important: the bridge already pre-flips fair_prob for the YRFI
    side (stores 1 - p there), but `build_output()` ALSO flips when
    `market_type=="YRFI"`. So we always pass the canonical NRFI
    probability and let `build_output` produce the correct side. Bug
    fixed in this PR — previously YRFI rows showed the same % as NRFI.
    """
    side_p = float(bridge_output.fair_prob)
    if bridge_output.market_type == "YRFI":
        nrfi_p = 1.0 - side_p
    else:
        nrfi_p = side_p

    # Friendly game label (e.g. "DET @ BOS") falls back to the gamePk
    # string if we don't have a mapping for this game.
    label = (label_map or {}).get(str(bridge_output.game_id),
                                    str(bridge_output.game_id))

    out = build_output(
        game_id=label,
        blended_p=nrfi_p,
        lambda_total=bridge_output.lambda_total,
        market_type=bridge_output.market_type,
        shap_drivers=bridge_output.shap_drivers,
        mc_low=bridge_output.mc_low,
        mc_high=bridge_output.mc_high,
        market_prob=bridge_output.market_prob,
        grade=bridge_output.grade,
        realization=bridge_output.realization,
        engine=engine_label,
    )

    # Tier classification — drives the [ELITE]/[STRONG]/[MODERATE]/[LEAN]
    # tag that prefixes each pick line in the email + dashboard.
    from edge_equation.engines.tiering import classify_tier
    tier_clf = classify_tier(
        market_type=out.market_type,
        side_probability=out.nrfi_prob,
    )

    pretty = to_email_card(out)
    if tier_clf.tier.is_qualifying:
        # Prepend the tier tag so the operator sees conviction at a glance.
        pretty_lines = pretty.split("\n", 1)
        pretty_lines[0] = f"[{tier_clf.tier.value:<8}] {pretty_lines[0]}"
        pretty = "\n".join(pretty_lines)

    return {
        "game_id": out.game_id,
        "market_type": out.market_type,
        "selection": out.market_type,
        "pct": out.nrfi_pct,
        "fair_prob": f"{out.nrfi_prob:.4f}",
        "color_band": out.color_band,
        "color_hex": out.color_hex,
        "signal": out.signal,
        "lambda_total": out.lambda_total,
        "mc_band_pp": out.mc_band_pp,
        "edge": (f"{out.edge_pp:+.1f}pp" if out.edge_pp is not None else None),
        # Phase NRFI-elite: keep the raw numeric edge so the top-board
        # renderer can sort by it without parsing the formatted string back.
        "edge_pp_raw": float(out.edge_pp) if out.edge_pp is not None else None,
        "kelly": (f"{out.kelly_units:.2f}u" if (out.kelly_units or 0) > 0 else None),
        "kelly_units_raw": float(out.kelly_units) if out.kelly_units is not None else None,
        "grade": out.grade,
        "tier": tier_clf.tier.value,
        "tier_basis": tier_clf.basis,
        "drivers": out.driver_text,
        "rendered": pretty,
    }


# ---------------------------------------------------------------------------
# NRFI-elite polish: top-N-by-edge board with cleaner one-liner format.
# ---------------------------------------------------------------------------


# Operator-friendly labels for the most common SHAP feature names. The
# raw column names are descriptive but unreadable in a public-facing
# email ("home_p_xera_t30"); this map converts to phrases a casual
# reader can parse at first glance. Unmapped features fall through
# unchanged so the renderer never hides information.
_DRIVER_NAME_MAP: dict[str, str] = {
    "home_p_xera":           "home pitcher xERA",
    "away_p_xera":           "away pitcher xERA",
    "home_p_xera_t30":       "home pitcher xERA (30d)",
    "away_p_xera_t30":       "away pitcher xERA (30d)",
    "home_xwoba_t90":        "home offense xwOBA (90d)",
    "away_xwoba_t90":        "away offense xwOBA (90d)",
    "home_k_pct":            "home K%",
    "away_k_pct":            "away K%",
    "home_bb_pct":           "home BB%",
    "away_bb_pct":           "away BB%",
    "k_pct":                 "strikeout rate",
    "bb_pct":                "walk rate",
    "ump_zone_idx":          "umpire zone size",
    "ump_run_environment":   "umpire run environment",
    "abs_overturn_rate":     "ABS challenge rate",
    "bb_pct_uplift":         "ABS walk uplift",
    "park_factor_runs":      "park run factor",
    "weather_temp_f":        "temperature",
    "weather_wind_speed":    "wind speed",
    "weather_wind_dir_deg":  "wind direction",
    "humidity_pct":          "humidity",
    "air_density":           "air density",
    "roof_open":             "roof status",
    "home_lineup_xwoba":     "home lineup xwOBA",
    "away_lineup_xwoba":     "away lineup xwOBA",
    "home_p_first_inn_era":  "home pitcher 1st-inning ERA",
    "away_p_first_inn_era":  "away pitcher 1st-inning ERA",
    "poisson_p_nrfi":        "baseline NRFI prior",
}


def _humanize_driver(driver: str) -> str:
    """Translate one `+4.2 home_xwoba_t90` driver string into something
    a casual reader can parse: "+4.2 home offense xwOBA (90d)".
    Unmapped features pass through unchanged.
    """
    # driver_text rows look like "+4.2 feature_name" or "−2.1 feature_name".
    parts = driver.split(" ", 1)
    if len(parts) != 2:
        return driver
    delta, name = parts
    friendly = _DRIVER_NAME_MAP.get(name.strip(), name.strip())
    return f"{delta} {friendly}"


def _polished_pick_line(pick: dict) -> str:
    """Render one pick in the elite-polish format used by the top board.

    Layout::

        BOS @ NYY · NRFI                                 [ELITE   ]
        78.4% Conviction · Electric Blue · λ 1.20  MC ±3.2pp  edge +5.1pp  stake 0.50u
        Why: home offense soft (+4.2), umpire pitcher-friendly (+2.1),
              cool air boost (+1.5)

    Side-aware color: Strong YRFI rows render the band as **Red** to
    match the "red-hot opportunity" framing. Every other tier/market
    falls through to the standard ladder.

    Robust to missing fields — every optional metric (MC, edge, Kelly,
    drivers) drops out cleanly when not present.
    """
    matchup = str(pick.get("game_id", "—"))
    tier = pick.get("tier", "")
    market = pick.get("market_type", "NRFI")
    pct = pick.get("pct", 0.0)
    lam = pick.get("lambda_total")

    # Side-aware band label — Strong YRFI gets Red.
    try:
        from edge_equation.engines.tiering import (
            Tier, color_band_label_for_pick,
        )
        tier_obj = Tier(tier) if tier else None
        band = color_band_label_for_pick(tier_obj, market) if tier_obj else ""
    except (ValueError, ImportError):
        band = pick.get("color_band", "")

    # Top line: matchup + market token + right-justified tier tag.
    headline = f"{matchup} · {market}" if market else matchup
    tier_tag = f"[{tier:<8}]" if tier else ""
    head = f"{headline:<48}{tier_tag}".rstrip()

    # Headline metric line — branding mandates "% Conviction" rather
    # than "% NRFI / % YRFI" so the brand voice stays analytical
    # ("we have 78.4% conviction") instead of result-flavored.
    metric_parts = [f"{pct:.1f}% Conviction"]
    if band:
        metric_parts.append(band)
    if isinstance(lam, (int, float)):
        metric_parts.append(f"λ {lam:.2f}")
    metric_line = " · ".join(metric_parts)

    extras: list[str] = []
    mc = pick.get("mc_band_pp")
    if isinstance(mc, (int, float)):
        extras.append(f"MC ±{mc:.1f}pp")
    edge = pick.get("edge")
    if edge:
        extras.append(f"edge {edge}")
    kelly = pick.get("kelly")
    if kelly:
        extras.append(f"stake {kelly}")
    if extras:
        metric_line += "  " + "  ".join(extras)

    drivers = pick.get("drivers") or []
    lines = [head, metric_line]
    if drivers:
        why = ", ".join(_humanize_driver(d) for d in drivers[:4])
        lines.append(f"  Why: {why}")
    return "\n".join(lines)


def _select_top_picks_by_edge(picks: list[dict], *, n: int = 8) -> list[dict]:
    """Pick the higher-tier side per game, sort by edge desc (NRFI%
    desc as tiebreak), return the top `n`.

    Falls back gracefully when no edge values are present (no live odds
    yet) — sorts purely by tier rank then NRFI%.
    """
    if not picks:
        return []
    # Group by game_id and keep the higher-tier side per game.
    tier_rank = {"ELITE": 4, "STRONG": 3, "MODERATE": 2,
                   "LEAN": 1, "NO_PLAY": 0}
    by_game: dict[str, dict] = {}
    for p in picks:
        gid = p.get("game_id", "")
        cur = by_game.get(gid)
        if cur is None:
            by_game[gid] = p
            continue
        if tier_rank.get(p.get("tier", ""), 0) > tier_rank.get(
                cur.get("tier", ""), 0):
            by_game[gid] = p
    one_per_game = list(by_game.values())
    one_per_game.sort(key=lambda p: (
        -tier_rank.get(p.get("tier", ""), 0),
        -(p.get("edge_pp_raw") or 0.0),
        -float(p.get("pct", 0.0)),
    ))
    return one_per_game[:n]


def _render_top_board(picks: list[dict], *, n: int = 8) -> str:
    """Plain-text top-N board with the polished pick lines."""
    top = _select_top_picks_by_edge(picks, n=n)
    if not top:
        return ""
    lines = [f"TOP BOARD — Top {len(top)} by Edge", "═" * 60]
    for i, pick in enumerate(top, 1):
        lines.append(f"{i:>2}.  {_polished_pick_line(pick)}".replace(
            "\n", "\n     ",  # indent continuation lines under the rank
        ))
        lines.append("")
    return "\n".join(lines)


def _build_parlay_block(
    bridge_outputs: list, label_map: dict[str, str], store,
) -> tuple[str, str]:
    """Convert bridge outputs into parlay legs + render top candidates.

    Returns (candidates_text, ledger_text). Both empty strings when no
    candidate qualifies (the typical day) or no tickets have been
    recorded yet. Caller swallows exceptions, so any error here just
    yields ("", "").
    """
    from edge_equation.engines.parlay import (
        ParlayConfig, ParlayLeg, build_parlay_candidates,
        init_parlay_tables, render_candidate,
        render_ledger_section as parlay_render_ledger,
    )
    from edge_equation.engines.tiering import Tier, classify_tier

    cfg = ParlayConfig()
    legs: list[ParlayLeg] = []
    for o in bridge_outputs:
        # The bridge stores `fair_prob` as the side's own probability
        # (NRFI prob for an NRFI row, YRFI prob for a YRFI row), so
        # we can hand it straight to classify_tier as side_probability.
        side_p = float(o.fair_prob)
        clf = classify_tier(market_type=o.market_type,
                              side_probability=side_p)
        if clf.tier not in (Tier.ELITE, Tier.STRONG):
            continue
        # Phase 4 swap-in lives elsewhere — for now use the engine's
        # flat default odds. The settlement pass uses captured odds
        # when present, so the units math stays honest at settle time
        # even if the email-displayed odds are the default.
        odds = -120.0 if o.market_type == "NRFI" else -105.0
        label = label_map.get(str(o.game_id), str(o.game_id))
        legs.append(ParlayLeg(
            market_type=o.market_type,
            side="Under 0.5" if o.market_type == "NRFI" else "Over 0.5",
            side_probability=side_p,
            american_odds=odds,
            tier=clf.tier,
            game_id=str(o.game_id),
            label=f"{label} {o.market_type}",
        ))

    candidates = build_parlay_candidates(legs, config=cfg)
    candidates_text = ""
    if candidates:
        # Top 2 — Special Drops are by definition rare.
        rendered = "\n\n".join(render_candidate(c) for c in candidates[:2])
        header = "PARLAY CANDIDATES (Special Drops)\n" + ("─" * 60)
        candidates_text = header + "\n" + rendered

    ledger_text = ""
    try:
        init_parlay_tables(store)
        ledger_text = parlay_render_ledger(store)
    except Exception as e:
        log.debug("parlay ledger render skipped (%s): %s",
                    type(e).__name__, e)
    return candidates_text, ledger_text


def _build_game_label_map(store, target_date: str) -> dict[str, str]:
    """Return {gamePk_str → "AWY @ HOM"} for every game on the slate.

    Best-effort: if the games table query fails (e.g. fresh DB), we
    return an empty dict and the renderer falls back to bare gamePks.
    """
    try:
        df = store.games_for_date(target_date)
    except Exception as e:
        log.warning("games_for_date(%s) failed: %s", target_date, e)
        return {}
    if df is None or df.empty:
        return {}
    out: dict[str, str] = {}
    for _, g in df.iterrows():
        try:
            home = str(getattr(g, "home_team", "") or "?")
            away = str(getattr(g, "away_team", "") or "?")
            pk = str(int(getattr(g, "game_pk", 0)))
            out[pk] = f"{away} @ {home}"
        except Exception:
            continue
    return out


def _footer_text(engine: str) -> str:
    return (
        f"Engine: {engine}.  Internal testing — not financial advice.  "
        "Edge Equation v1 — generated by nrfi.email_report."
    )


# ---------------------------------------------------------------------------
# Body rendering — overrides EmailPublisher's default for richer NRFI text
# ---------------------------------------------------------------------------

def render_body(card: dict) -> str:
    """Render the full plain-text email body from a card dict.

    Layout (production-elite ordering, post-rebrand):
      1. Header + Conviction key (legend explaining color tokens)
      2. YTD per-tier ledger (always rendered)
      3. TOP BOARD — top 8 picks by edge with polished one-liner
      4. Per-side NRFI / YRFI boards (full slate)
      5. Parlay candidates (Phase 6)
      6. Parlay ledger (Phase 6)
      7. Props block + props ledger
      8. Full-game block + full-game ledger
      9. Footer (premium disclaimer when card_type=premium)
    """
    from edge_equation.engines.tiering import (
        render_conviction_key, render_premium_disclaimer,
    )

    lines = [
        f"Edge Equation — {card.get('headline', 'NRFI/YRFI Daily')}",
        card.get("subhead", "Facts. Not Feelings."),
        "",
    ]

    # 1b. Conviction key — operator + forwarded reader can decode the
    # color tokens at a glance. Stays at the top so the rest of the
    # body can reference the language without re-explaining.
    lines.append(render_conviction_key())
    lines.append("")

    # 2. YTD per-tier ledger — top of page so the operator sees
    # conviction history before today's picks. Always rendered, even
    # when empty, with a friendly placeholder so the section exists.
    ledger_text = card.get("ledger_text", "")
    if ledger_text:
        lines.append(ledger_text)
        lines.append("")
    else:
        season = (card.get("target_date") or "")[:4] or "—"
        lines.append(f"YTD LEDGER ({season})")
        lines.append("─" * 60)
        lines.append("  (no settled picks yet — ledger populates after "
                       "the first qualifying game finishes)")
        lines.append("")

    picks = card.get("picks", []) or []
    if not picks:
        lines.append("(No games on the slate or feature reconstruction failed.)")
        lines.append("")
    else:
        # 3. TOP BOARD — top N (default 8) by edge then NRFI%, polished
        # one-liner with tier + color + λ + MC + edge + Kelly. The
        # operator's eye lands here first.
        top_text = _render_top_board(picks, n=8)
        if top_text:
            lines.append(top_text)
            lines.append("")

        # 4. Per-side boards — classic full-slate rendering (kept for
        # operators who want every game's NRFI + YRFI rows to scan).
        nrfi = [p for p in picks if p["market_type"] == "NRFI"]
        yrfi = [p for p in picks if p["market_type"] == "YRFI"]
        if nrfi:
            lines.append(f"NRFI BOARD ({len(nrfi)} games)")
            lines.append("─" * 60)
            for p in nrfi:
                rendered = p.get("rendered") or _render_fallback(p)
                lines.append(rendered)
                lines.append("")
        if yrfi:
            lines.append(f"YRFI BOARD ({len(yrfi)} games)")
            lines.append("─" * 60)
            for p in yrfi:
                rendered = p.get("rendered") or _render_fallback(p)
                lines.append(rendered)
                lines.append("")

    # 5. Parlay candidates (already top-2 capped in _build_parlay_block).
    parlay_text = card.get("parlay_text", "")
    if parlay_text:
        lines.append("")
        lines.append(parlay_text)
        lines.append("")

    # 6. Parlay ledger summary (Phase 6).
    parlay_ledger_text = card.get("parlay_ledger_text", "")
    if parlay_ledger_text:
        lines.append("")
        lines.append(parlay_ledger_text)
        lines.append("")

    # 7. Props block (Props-3). Top picks by edge + per-tier YTD ledger.
    # Lives below the NRFI parlay section so the operator sees first-
    # inning conviction first, then cross-market edges, then the props
    # YTD record.
    props_top_text = card.get("props_top_text", "")
    if props_top_text:
        lines.append("")
        lines.append(props_top_text)
        lines.append("")

    props_ledger_text = card.get("props_ledger_text", "")
    if props_ledger_text:
        lines.append("")
        lines.append(props_ledger_text)
        lines.append("")

    # 8. Full-game block (FG-3). Top picks by edge across the four
    # supported full-game markets + per-tier YTD ledger. Lives below
    # props so the email reads top → bottom: NRFI → parlays → props
    # → full-game → footer.
    fullgame_top_text = card.get("fullgame_top_text", "")
    if fullgame_top_text:
        lines.append("")
        lines.append(fullgame_top_text)
        lines.append("")

    fullgame_ledger_text = card.get("fullgame_ledger_text", "")
    if fullgame_ledger_text:
        lines.append("")
        lines.append(fullgame_ledger_text)
        lines.append("")

    lines.append(card.get("tagline", "")
                  or _footer_text(card.get("engine", "unknown")))

    # Premium disclaimer — surfaced when the operator has flagged the
    # card as the premium tier. Free-tier emails skip this block; the
    # `Facts. Not Feelings.` subhead at the top already carries the
    # voice for the public daily.
    if card.get("card_type") == "premium" or card.get("premium"):
        lines.append("")
        lines.append(render_premium_disclaimer())

    return "\n".join(lines)


def _render_fallback(p: dict) -> str:
    return (f"{p['market_type']:<5} {p.get('pct', 0):.1f}% [{p.get('color_band','?')}]  "
            f"λ={p.get('lambda_total', 0):.2f}  game={p.get('game_id', '?')}")


def build_subject(card: dict) -> str:
    return f"{SUBJECT_PREFIX} — {card.get('target_date', _date.today().isoformat())}"


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_email(card: dict, *, recipient: Optional[str] = None,
               dry_run: bool = False) -> dict[str, Any]:
    """Send (or simulate) the NRFI daily email.

    Returns a dict with `success` / `target` / `subject` for callers
    that want to log the outcome. Honours `--dry-run` by skipping the
    SMTP step and printing the body to stdout.
    """
    body = render_body(card)
    subject = build_subject(card)
    target = (recipient
              or os.environ.get("EMAIL_TO")
              or os.environ.get("SMTP_TO")
              or "ProfessorEdgeCash@gmail.com")

    if dry_run:
        print(f"--- DRY RUN ---\nTo: {target}\nSubject: {subject}\n")
        print(body)
        return {"success": True, "target": target, "subject": subject, "dry_run": True}

    # Lazy import — keep nrfi/ from hard-depending on the publishing
    # layer when called as a library.
    from edge_equation.publishing.email_publisher import EmailPublisher

    # Build a publisher that uses our richer renderer instead of the
    # default ML/Total-shaped builder.
    publisher = EmailPublisher(
        body_formatter=lambda c: render_body(c),
        subject_prefix=SUBJECT_PREFIX,
    )
    # Override recipient if user supplied one.
    if recipient:
        os.environ["EMAIL_TO"] = recipient
        publisher = EmailPublisher(
            body_formatter=lambda c: render_body(c),
            subject_prefix=SUBJECT_PREFIX,
        )

    result = publisher.publish_card(card, dry_run=False)
    return {
        "success": getattr(result, "success", False),
        "target": getattr(result, "target", target),
        "subject": subject,
        "error": getattr(result, "error", None),
        "failsafe_triggered": getattr(result, "failsafe_triggered", False),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NRFI/YRFI daily email report")
    parser.add_argument("--date", default=_date.today().isoformat(),
                        help="Slate date (YYYY-MM-DD; default: today)")
    parser.add_argument("--to", default=None,
                        help="Recipient address (overrides EMAIL_TO env var)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the card and print to stdout; skip SMTP")
    parser.add_argument("--no-etl", action="store_true",
                        help="Skip the daily schedule fetch (use cached games)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    card = build_card(args.date, run_etl=not args.no_etl)
    result = send_email(card, recipient=args.to, dry_run=args.dry_run)

    if result.get("success"):
        log.info("Email sent: target=%s subject=%s",
                 result.get("target"), result.get("subject"))
        return 0
    log.error("Email send failed: %s", result.get("error"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
