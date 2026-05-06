"""Unified MLB daily runner.

A single entry point — operator runs ONE command and gets the entire
MLB card for the day:

* NRFI / YRFI top board + ledger.
* Full-game (Moneyline / Run Line / Total / Team Total / F5_Total /
  F5_ML) top board + ledger.
* Player props (Hits / RBI / HR / K / Total Bases / Runs / SB / …)
  top board + ledger.
* Game-results parlay engine card (3–6 legs, strict).
* Player-props parlay engine card (3–6 legs, strict).

The runner is a thin orchestrator — every engine still owns its math,
context, and persistence layers. We import the per-engine builders
here, run them in series, then hand the resulting outputs to the two
new parlay engines so they have a coherent "today's data" snapshot to
combine from.

CLI::

    python -m edge_equation.engines.mlb.run_daily
    python -m edge_equation.engines.mlb.run_daily --date 2026-05-06
    python -m edge_equation.engines.mlb.run_daily --include-alternates
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger

from .game_results_parlay import (
    GameResultsParlayCard,
    MLBGameResultsParlayEngine,
)
from .player_props_parlay import (
    MLBPlayerPropsParlayEngine,
    PlayerPropsParlayCard,
)
from .thresholds import MLB_PARLAY_RULES, MLBParlayRules

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Output payload
# ---------------------------------------------------------------------------


@dataclass
class UnifiedMLBCard:
    """The full MLB daily card. Everything the operator's email + the
    website exporter need to render today's slate.

    Each per-engine card is a thin pass-through of the engine's own
    `*Card` dataclass (NRFI text, props PropsCard, full-game
    FullGameCard) so the runner doesn't impose a new schema. The two
    parlay cards are the new additions.
    """

    target_date: str
    nrfi_text: str = ""
    full_game_card: Optional[object] = None
    props_card: Optional[object] = None
    game_results_parlay_card: Optional[GameResultsParlayCard] = None
    player_props_parlay_card: Optional[PlayerPropsParlayCard] = None

    @property
    def has_any_picks(self) -> bool:
        return any([
            bool(self.nrfi_text),
            bool(self.full_game_card and getattr(
                self.full_game_card, "picks", []
            )),
            bool(self.props_card and getattr(
                self.props_card, "picks", []
            )),
            bool(
                self.game_results_parlay_card
                and self.game_results_parlay_card.has_qualified
            ),
            bool(
                self.player_props_parlay_card
                and self.player_props_parlay_card.has_qualified
            ),
        ])


# ---------------------------------------------------------------------------
# Per-engine runners — best-effort wrappers
# ---------------------------------------------------------------------------


def _run_full_game(target_date: str, *, include_alternates: bool):
    """Run the full-game engine for ``target_date``. Returns a
    `FullGameCard` or `None` on failure."""
    try:
        from edge_equation.engines.full_game.daily import build_full_game_card
        return build_full_game_card(
            target_date,
            include_alternates=include_alternates,
        )
    except Exception as e:
        log.warning(
            "MLB unified runner: full-game engine failed (%s): %s",
            type(e).__name__, e,
        )
        return None


def _run_props(target_date: str):
    """Run the props engine for ``target_date``."""
    try:
        from edge_equation.engines.props_prizepicks.daily import build_props_card
        return build_props_card(target_date)
    except Exception as e:
        log.warning(
            "MLB unified runner: props engine failed (%s): %s",
            type(e).__name__, e,
        )
        return None


def _run_nrfi_card(target_date: str) -> tuple[str, list[dict]]:
    """Run the NRFI/YRFI engine and return (top-board text, prediction
    rows). Falls back to empty values when the optional NRFI extras
    aren't installed.

    The NRFI engine writes one prediction row per game with the
    NRFI-side probability; the parlay engine derives both NRFI and
    YRFI legs from each row.
    """
    try:
        from edge_equation.engines.nrfi.config import get_default_config
        from edge_equation.engines.nrfi.data.storage import NRFIStore
        cfg = get_default_config().resolve_paths()
        store = NRFIStore(cfg.duckdb_path)
        try:
            df = store.query_df(
                """
                SELECT p.game_pk, p.nrfi_prob, p.nrfi_pct, p.lambda_total,
                       p.color_band, p.market_prob, p.edge,
                       g.away_team, g.home_team, g.game_date
                FROM predictions p
                LEFT JOIN games g ON g.game_pk = p.game_pk
                WHERE g.game_date = ?
                """,
                (target_date,),
            )
            if df is None or len(df) == 0:
                return "", []
            rows: list[dict] = []
            for _, r in df.iterrows():
                rows.append({
                    "game_pk": int(r.get("game_pk") or 0),
                    "nrfi_prob": float(r.get("nrfi_prob") or 0.0),
                    "market_prob": (
                        float(r.get("market_prob"))
                        if r.get("market_prob") == r.get("market_prob")
                        and r.get("market_prob") is not None
                        else None
                    ),
                    "away_team": str(r.get("away_team") or ""),
                    "home_team": str(r.get("home_team") or ""),
                    "color_band": str(r.get("color_band") or ""),
                    "market_type": (
                        "NRFI" if float(r.get("nrfi_prob") or 0.0) >= 0.5
                        else "YRFI"
                    ),
                })
            text = _render_nrfi_top_board(rows)
            return text, rows
        finally:
            store.close()
    except Exception as e:
        log.info(
            "MLB unified runner: NRFI extras unavailable (%s): %s — "
            "skipping NRFI rows in parlay pool.",
            type(e).__name__, e,
        )
        return "", []


def _render_nrfi_top_board(rows: Sequence[dict]) -> str:
    """Mini NRFI top board for the unified card output."""
    if not rows:
        return ""
    out = [f"NRFI / YRFI BOARD — {len(rows)} games", "═" * 60]
    for r in rows:
        prob = float(r.get("nrfi_prob", 0.0))
        side = "NRFI" if prob >= 0.5 else "YRFI"
        side_pct = prob * 100.0 if side == "NRFI" else (1.0 - prob) * 100.0
        out.append(
            f"  {r.get('away_team',''):<4} @ {r.get('home_team',''):<4}  "
            f"{side} {side_pct:5.1f}%   band: "
            f"{r.get('color_band','')}"
        )
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_unified_mlb_card(
    target_date: Optional[str] = None,
    *,
    include_alternates: bool = False,
    rules: MLBParlayRules = MLB_PARLAY_RULES,
    top_n_parlays: int = 3,
) -> UnifiedMLBCard:
    """Build the unified MLB daily card.

    Pipeline:
    1. Run NRFI/YRFI → text top board + per-game prediction rows.
    2. Run full-game engine → ``FullGameCard``.
    3. Run player-props engine → ``PropsCard``.
    4. Feed (full_game.picks + nrfi rows) into the game-results parlay
       engine.
    5. Feed (props.picks) into the player-props parlay engine.

    The parlay engines see today's already-projected outputs, so they
    inherit the per-engine confidence floors, calibration shrinks, and
    closing-line snapshots without any double work.
    """
    target = target_date or _date.today().isoformat()

    nrfi_text, nrfi_rows = _run_nrfi_card(target)
    full_game_card = _run_full_game(target, include_alternates=include_alternates)
    props_card = _run_props(target)

    full_game_picks = (
        list(getattr(full_game_card, "picks", []) or [])
        if full_game_card is not None else []
    )
    prop_picks = (
        list(getattr(props_card, "picks", []) or [])
        if props_card is not None else []
    )

    game_engine = MLBGameResultsParlayEngine(rules=rules, top_n=top_n_parlays)
    game_card = game_engine.run(
        full_game_outputs=full_game_picks,
        nrfi_rows=nrfi_rows,
        target_date=target,
    )

    props_engine = MLBPlayerPropsParlayEngine(rules=rules, top_n=top_n_parlays)
    props_parlay_card = props_engine.run(
        prop_outputs=prop_picks,
        target_date=target,
    )

    return UnifiedMLBCard(
        target_date=target,
        nrfi_text=nrfi_text,
        full_game_card=full_game_card,
        props_card=props_card,
        game_results_parlay_card=game_card,
        player_props_parlay_card=props_parlay_card,
    )


# ---------------------------------------------------------------------------
# Engine-registry runner — tiny wrapper so the central registry can
# treat the unified runner like any other engine.
# ---------------------------------------------------------------------------


@dataclass
class MLBDailyRunner:
    """Engine-registry façade for the unified MLB daily runner."""

    rules: MLBParlayRules = MLB_PARLAY_RULES
    include_alternates: bool = False
    top_n_parlays: int = 3

    name: str = "mlb_daily"

    def run(self, target_date: Optional[str] = None) -> UnifiedMLBCard:
        return build_unified_mlb_card(
            target_date,
            include_alternates=self.include_alternates,
            rules=self.rules,
            top_n_parlays=self.top_n_parlays,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_card(card: UnifiedMLBCard) -> str:
    """Plain-text rendering of the unified card for the operator's
    terminal / log."""
    parts: list[str] = [
        f"MLB DAILY CARD — {card.target_date}",
        "=" * 60,
        "",
    ]
    if card.nrfi_text:
        parts.append(card.nrfi_text)
    if card.full_game_card is not None:
        text = getattr(card.full_game_card, "top_board_text", "") or ""
        if text:
            parts.append(text)
        ledger = getattr(card.full_game_card, "ledger_text", "") or ""
        if ledger:
            parts.append(ledger)
    if card.props_card is not None:
        text = getattr(card.props_card, "top_board_text", "") or ""
        if text:
            parts.append(text)
        ledger = getattr(card.props_card, "ledger_text", "") or ""
        if ledger:
            parts.append(ledger)
    if card.game_results_parlay_card is not None:
        parts.append(card.game_results_parlay_card.top_board_text)
    if card.player_props_parlay_card is not None:
        parts.append(card.player_props_parlay_card.top_board_text)
    if not card.has_any_picks:
        parts.append(
            "  No picks for this slate — engines were run but produced "
            "no qualifying entries."
        )
    return "\n".join(parts).rstrip() + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the unified MLB daily card (all markets + parlays).",
    )
    parser.add_argument(
        "--date", default=None,
        help="Slate date YYYY-MM-DD. Default: today (UTC).",
    )
    parser.add_argument(
        "--include-alternates", action="store_true",
        help="Pull alternate Run_Line / Total / Team_Total lines via "
        "the per-event endpoint (extra Odds API credits).",
    )
    parser.add_argument(
        "--top-n-parlays", type=int, default=3,
        help="Maximum parlay candidates to surface per universe.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    card = build_unified_mlb_card(
        args.date,
        include_alternates=args.include_alternates,
        top_n_parlays=args.top_n_parlays,
    )
    print(_format_card(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
