"""MLB player-profile writer.

For each player on today's MLB slate, builds two JSON files:

* ``mlb/player_logs/<slug>.json``    — last-N per-game stat lines.
* ``mlb/context_today/<slug>.json``  — today's live context.

Statcast is the source for game logs:

  - ``PlayerIdResolver`` (already used by the props engine) maps the
    player's display name to an MLBAM id with a persistent JSON cache.
  - ``fetch_player_statcast_window`` (already used by the props
    engine) pulls pitch-by-pitch Statcast events for a 60-day rolling
    window, parquet-cached so a re-run is free.
  - We aggregate the pitch-level frame to per-game rows via pandas
    groupby on ``game_date``. Output stat dictionary covers the
    box-score basics: AB, H, HR, RBI, BB, K, plus xBA / xSLG when
    Statcast surfaces them.

Best-effort throughout. When pybaseball isn't installed, when the
player's MLBAM id can't be resolved, when the Statcast frame is
empty — the writer drops a "Limited data" stub instead of an empty
file so the website still has something to render.
"""

from __future__ import annotations

from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

from edge_equation.engines.props_prizepicks.config import (
    PropsConfig, get_default_config,
)
from edge_equation.engines.props_prizepicks.data.player_id_lookup import (
    PlayerIdResolver,
)
from edge_equation.engines.props_prizepicks.data.statcast_loader import (
    fetch_player_statcast_window,
)
from edge_equation.utils.logging import get_logger

from .writer import (
    ContextItem,
    GameLog,
    GameLogRow,
    TodaysContext,
    empty_context,
    empty_log,
    write_player_log,
    write_today_context,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public entry — called by the master daily runner.
# ---------------------------------------------------------------------------


def write_profiles_for_slate(
    *,
    target_date: Optional[str] = None,
    player_names: Iterable[str],
    role: str = "batter",
    days: int = 60,
    config: Optional[PropsConfig] = None,
    out_root: Optional[Path] = None,
) -> dict[str, int]:
    """Write player_logs + context_today JSON for every player in
    ``player_names``. Returns ``{"logs": N, "context": M}`` counts.

    ``role`` is the Statcast routing flag — pass ``"batter"`` for
    hitters or ``"pitcher"`` for pitchers; the Statcast endpoint
    differs. Today the daily runner calls this once for batters and
    once for pitchers.
    """
    cfg = (config or get_default_config()).resolve_paths()
    target = target_date or _date.today().isoformat()
    resolver = PlayerIdResolver(cfg.cache_dir / "player_ids.json")
    counts = {"logs": 0, "context": 0}

    for raw_name in dedupe(player_names):
        if not raw_name:
            continue
        try:
            mlbam_id = resolver.resolve(raw_name)
        except Exception as e:
            log.debug(
                "MLB player-profile: id lookup failed for %r (%s): %s",
                raw_name, type(e).__name__, e,
            )
            mlbam_id = None

        # Game log — Statcast aggregation when possible.
        log_obj = empty_log(raw_name)
        if mlbam_id is not None:
            try:
                df = fetch_player_statcast_window(
                    mlbam_id, end_date=target, days=days,
                    role=role, config=cfg,
                )
                if df is not None and not df.empty:
                    log_obj = aggregate_statcast_to_game_log(
                        raw_name, df, role=role,
                    )
            except Exception as e:
                log.debug(
                    "MLB player-profile: Statcast aggregation failed for "
                    "%r (%s): %s",
                    raw_name, type(e).__name__, e,
                )

        try:
            write_player_log("mlb", log_obj, out_root=out_root)
            counts["logs"] += 1
        except Exception as e:
            log.warning(
                "MLB player-profile: write_player_log failed for %r "
                "(%s): %s",
                raw_name, type(e).__name__, e,
            )

        # Today's context — minimal but honest snapshot.
        ctx = build_today_context(
            raw_name, role=role, target_date=target, mlbam_id=mlbam_id,
        )
        try:
            write_today_context("mlb", ctx, out_root=out_root)
            counts["context"] += 1
        except Exception as e:
            log.warning(
                "MLB player-profile: write_today_context failed for %r "
                "(%s): %s",
                raw_name, type(e).__name__, e,
            )

    try:
        resolver.save()
    except Exception:
        pass

    log.info(
        "MLB player-profile: wrote %d game-log files and %d context files",
        counts["logs"], counts["context"],
    )
    return counts


# ---------------------------------------------------------------------------
# Statcast → per-game aggregation.
# ---------------------------------------------------------------------------


def aggregate_statcast_to_game_log(
    player_name: str, df, *, role: str,
) -> GameLog:
    """Aggregate a Statcast pitch-level DataFrame to per-game rows.

    Columns we expect when present on the Statcast frame:
    ``game_date``, ``home_team``, ``away_team``, ``inning_topbot``,
    ``events``, ``estimated_ba_using_speedangle`` (xBA), and
    ``estimated_slg_using_speedangle`` (xSLG). Missing columns are
    handled gracefully — the resulting stat dict just drops those
    keys for that game.

    Sorted newest first so the website's last-5/10/20 toggle picks
    up the most recent appearances by default.
    """
    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        log.debug("pandas unavailable — returning empty MLB game log.")
        return empty_log(player_name)

    if df is None or df.empty or "game_date" not in df.columns:
        return empty_log(player_name)

    # Coerce game_date to ISO string for both groupby + JSON output.
    df = df.copy()
    df["game_date"] = (
        df["game_date"].astype(str).str.slice(0, 10)
    )

    rows: list[GameLogRow] = []
    for game_date, group in df.groupby("game_date", sort=False):
        opp = _opponent_for(group, role=role)
        is_home = bool(_is_home_for(group, role=role))
        if role == "batter":
            stats = _batter_stats_from_group(group)
        else:
            stats = _pitcher_stats_from_group(group)
        rows.append(GameLogRow(
            date=str(game_date),
            opponent=str(opp or ""),
            is_home=is_home,
            result=None,           # win/loss isn't reliably in Statcast
            stats=stats,
        ))

    rows.sort(key=lambda r: r.date, reverse=True)
    return GameLog(player=player_name, rows=rows)


def _opponent_for(group, *, role: str) -> str:
    """Return the tricode of the opponent given the player's role.

    For a batter, the opponent is the FIELDING team — i.e. the
    pitcher's team, which is `home_team` if the half-inning is
    bottom (visiting batter) and `away_team` if top.
    """
    if "home_team" not in group.columns or "away_team" not in group.columns:
        return ""
    home = str(group["home_team"].iloc[0])
    away = str(group["away_team"].iloc[0])
    inning_topbot = (
        str(group.get("inning_topbot", "Top").iloc[0])
        if "inning_topbot" in group.columns else "Top"
    )
    is_top = inning_topbot.startswith("Top")
    if role == "batter":
        return home if is_top else away
    return away if is_top else home


def _is_home_for(group, *, role: str) -> bool:
    if "home_team" not in group.columns or "away_team" not in group.columns:
        return False
    inning_topbot = (
        str(group.get("inning_topbot", "Top").iloc[0])
        if "inning_topbot" in group.columns else "Top"
    )
    is_top = inning_topbot.startswith("Top")
    if role == "batter":
        return not is_top
    return is_top


def _batter_stats_from_group(group) -> dict:
    """Build a box-score-shaped stat dict from one game's Statcast PAs.

    Statcast `events` rows tag the terminal event of each plate
    appearance. We count each terminal event at most once per PA.
    """
    if "events" not in group.columns:
        return {}
    events = (
        group["events"].dropna().astype(str)
    )
    n_pa = int((events != "").sum())
    hits = int(events.isin([
        "single", "double", "triple", "home_run",
    ]).sum())
    hr = int((events == "home_run").sum())
    bb = int(events.isin(["walk", "hit_by_pitch"]).sum())
    so = int(events.isin([
        "strikeout", "strikeout_double_play",
    ]).sum())
    rbi_col = group.get("post_bat_score", None)
    rbi = 0
    if rbi_col is not None:
        try:
            # Approximate: max post-PA team score delta. Not perfect
            # — Statcast doesn't expose RBI directly — so we skip
            # rather than guess wrong.
            rbi = 0
        except Exception:
            rbi = 0
    out: dict = {
        "PA":  n_pa,
        "AB":  max(0, n_pa - bb),
        "H":   hits,
        "HR":  hr,
        "BB":  bb,
        "K":   so,
    }
    # xBA / xSLG when present — Statcast surfaces these on
    # contact-result rows.
    if "estimated_ba_using_speedangle" in group.columns:
        try:
            xba = float(
                group["estimated_ba_using_speedangle"].dropna().mean(),
            )
            if xba == xba:
                out["xBA"] = round(xba, 3)
        except Exception:
            pass
    if "estimated_slg_using_speedangle" in group.columns:
        try:
            xslg = float(
                group["estimated_slg_using_speedangle"].dropna().mean(),
            )
            if xslg == xslg:
                out["xSLG"] = round(xslg, 3)
        except Exception:
            pass
    return out


def _pitcher_stats_from_group(group) -> dict:
    """Per-game pitcher stat dict.

    Like the batter helper, we work off the `events` column. RA/ER
    aren't reliably exposed by Statcast at the pitch level so we
    surface what IS available (BF, K, BB, H, HR allowed).
    """
    if "events" not in group.columns:
        return {}
    events = group["events"].dropna().astype(str)
    n_bf = int((events != "").sum())
    h = int(events.isin([
        "single", "double", "triple", "home_run",
    ]).sum())
    hr = int((events == "home_run").sum())
    bb = int(events.isin(["walk", "hit_by_pitch"]).sum())
    so = int(events.isin([
        "strikeout", "strikeout_double_play",
    ]).sum())
    return {
        "BF":  n_bf,
        "H":   h,
        "HR":  hr,
        "BB":  bb,
        "K":   so,
    }


# ---------------------------------------------------------------------------
# Today's context.
# ---------------------------------------------------------------------------


def build_today_context(
    player_name: str, *,
    role: str,
    target_date: str,
    mlbam_id: Optional[int],
) -> TodaysContext:
    """Build a minimal but honest context snapshot for a player.

    Sources today are intentionally narrow: role tag, MLBAM id, and
    target date. The full lineup / weather / umpire layer lives in
    the engine's own context loaders — a follow-up PR can wire those
    in once the writer hookups are stable.
    """
    items: list[ContextItem] = [
        ContextItem(
            label="Slate role",
            value=role.capitalize(),
        ),
        ContextItem(
            label="MLBAM id",
            value=str(mlbam_id) if mlbam_id else "Unknown — name not resolved",
        ),
        ContextItem(
            label="Slate date",
            value=target_date,
        ),
        ContextItem(
            label="Source",
            value=(
                "Statcast pitch-by-pitch (60-day rolling window)"
                if mlbam_id is not None
                else "Limited data — name → MLBAM id lookup pending"
            ),
        ),
    ]
    return TodaysContext(player=player_name, items=items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def dedupe(names: Iterable[str]) -> list[str]:
    """Stable de-dupe — order preserved so a follow-up writer that
    re-reads the list sees deterministic order."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        n = (n or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out
