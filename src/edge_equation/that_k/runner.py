"""
That K Report -- orchestrator.

Walks a list of slate rows (pitcher + lineup + context + book K line)
and returns a KReportRow per starter.  Side-project glue; does NOT
touch the main engine's BettingEngine / PostingFormatter pipeline so
iteration here can't regress the main daily flow.

The slate row shape is plain dict so the runner stays easy to hook
to any data source we wire up later (CSV, a K-prop scraper, or an
MLB stat feed).  The sample_slate module ships a deterministic
dry-run slate matching this shape exactly.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, List, Sequence

from edge_equation.that_k.model import (
    GameContext,
    OpponentLineup,
    PitcherProfile,
    project_strikeouts,
)
from edge_equation.that_k.report import KReportRow, grade_row
from edge_equation.that_k.simulator import DEFAULT_N_SIMS, simulate_strikeouts


def _pitcher_from_row(row: dict) -> PitcherProfile:
    p = row.get("pitcher") or {}
    return PitcherProfile(
        name=p["name"],
        team=p["team"],
        throws=p.get("throws", "R"),
        k_per_bf=float(p.get("k_per_bf", 0.235)),
        expected_bf=float(p.get("expected_bf", 24.0)),
        swstr_pct=p.get("swstr_pct"),
        csw_pct=p.get("csw_pct"),
        arsenal=dict(p.get("arsenal") or {}),
        recent_k_per_bf=[
            (float(v), int(age))
            for (v, age) in (p.get("recent_k_per_bf") or [])
        ],
    )


def _lineup_from_row(row: dict) -> OpponentLineup:
    l = row.get("lineup") or {}
    return OpponentLineup(
        team=l["team"],
        swstr_pct=float(l.get("swstr_pct", 0.110)),
        csw_pct=float(l.get("csw_pct", 0.290)),
        lhh_share=float(l.get("lhh_share", 0.42)),
        pitch_whiff=dict(l.get("pitch_whiff") or {}),
        swstr_vs_L=l.get("swstr_vs_L"),
        swstr_vs_R=l.get("swstr_vs_R"),
    )


def _context_from_row(row: dict) -> GameContext:
    c = row.get("context") or {}
    return GameContext(
        dome=bool(c.get("dome", False)),
        temp_f=c.get("temp_f"),
        wind_mph=c.get("wind_mph"),
        wind_dir=c.get("wind_dir"),
        umpire_name=c.get("umpire_name"),
        umpire_k_factor=float(c.get("umpire_k_factor", 1.0)),
        park_k_factor=float(c.get("park_k_factor", 1.0)),
    )


def build_projections(
    slate_rows: Sequence[dict],
    *,
    n_sims: int = DEFAULT_N_SIMS,
    game_id_key: str = "game_id",
) -> List[KReportRow]:
    """Produce KReportRow for every row in the slate.

    Each slate row must contain:
      game_id: str
      line: float              -- sportsbook K line
      pitcher: dict            -- PitcherProfile shape
      lineup: dict             -- OpponentLineup shape
      context: dict            -- GameContext shape

    Returns rows ordered as the slate provided; callers sort for
    display (render_report ranks by edge_prob).
    """
    out: List[KReportRow] = []
    for row in slate_rows:
        pitcher = _pitcher_from_row(row)
        lineup = _lineup_from_row(row)
        context = _context_from_row(row)
        line = float(row["line"])
        inputs = project_strikeouts(pitcher, lineup, context)
        projection = simulate_strikeouts(
            pitcher=pitcher.name,
            team=pitcher.team,
            opponent=lineup.team,
            line=line,
            mean=inputs.projected_mean,
            dispersion=inputs.nb_dispersion,
            seed_key=str(row.get(game_id_key, "")),
            n_sims=n_sims,
        )
        row_out = KReportRow(
            projection=projection,
            inputs=inputs,
            pitcher=pitcher,
            lineup=lineup,
            context=context,
            grade=grade_row(projection),
        )
        out.append(row_out)
    return out
