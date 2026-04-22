"""
Brand-compliant text-only play formatter.

Phase 20 locks the free-content play format to this exact shape:

    LAA @ NYY - Home Run
    Market Consensus: Judge over 0.5 (+280)
    EE Projection: Grade A+
    Read: Barrel rate up 5pp last 2 weeks; fastball pitcher; wind out

Rules baked in:
  - Facts Not Feelings. No tout language ("smashes / locks / plays /
    value / sharp / signals / picks / bets"). Analytical data only.
  - Grade only, no edge percentage or Kelly sizing.
  - Read is a short ; -separated analytical note.
  - No emoji, no hashtags at the play level (hashtags live at the bottom
    of the card and are capped at two per post).
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from edge_equation.engine.pick_schema import Pick


@dataclass(frozen=True)
class PlayTextInputs:
    """Minimal, explicit inputs the formatter needs -- keeps the renderer
    decoupled from Pick's internal structure."""
    away_team: str
    home_team: str
    market_label: str           # "Home Run" / "Moneyline" / "Total" / etc.
    selection_label: str        # "Judge over 0.5"
    odds_str: str               # "+280" / "-110" / ""
    grade: str                  # "A+" / "A" / etc.
    read_notes: str             # "Barrel rate up 5pp last 2 weeks; ..."

    def to_dict(self) -> dict:
        return {
            "away_team": self.away_team,
            "home_team": self.home_team,
            "market_label": self.market_label,
            "selection_label": self.selection_label,
            "odds_str": self.odds_str,
            "grade": self.grade,
            "read_notes": self.read_notes,
        }


# Friendly labels for the engine's internal market_type codes. Keep these
# analytical ("Home Run", "Moneyline") -- NOT tout language like "pick"
# or "play".
MARKET_LABEL = {
    "ML": "Moneyline",
    "Run_Line": "Run Line",
    "Puck_Line": "Puck Line",
    "Spread": "Spread",
    "Total": "Total",
    "Game_Total": "Total",
    "HR": "Home Run",
    "K": "Strikeouts",
    "Passing_Yards": "Passing Yards",
    "Rushing_Yards": "Rushing Yards",
    "Receiving_Yards": "Receiving Yards",
    "Points": "Points",
    "Rebounds": "Rebounds",
    "Assists": "Assists",
    "SOG": "Shots on Goal",
    "BTTS": "Both Teams to Score",
    "NRFI": "No Runs First Inning",
    "YRFI": "Yes Run First Inning",
}


def _format_american_odds(odds) -> str:
    if odds is None:
        return ""
    try:
        n = int(odds)
    except (TypeError, ValueError):
        return str(odds)
    return f"+{n}" if n > 0 else str(n)


def build_play_text_inputs(
    pick: Pick,
    away_team: Optional[str] = None,
    home_team: Optional[str] = None,
    read_notes: str = "",
) -> PlayTextInputs:
    """
    Translate a Pick into the minimal inputs needed for render_play(). The
    away/home team names default to the pick's metadata-embedded values
    when the caller doesn't pass them explicitly.
    """
    meta = pick.metadata or {}
    home = home_team or meta.get("home_team") or ""
    away = away_team or meta.get("away_team") or ""
    market_label = MARKET_LABEL.get(pick.market_type, pick.market_type)
    # Selection label: for O/U markets include the point (line number)
    # so the reader doesn't need the raw market line.
    line_number = None
    if pick.line is not None and pick.line.number is not None:
        line_number = str(pick.line.number)
    selection_label = pick.selection
    if line_number and line_number not in selection_label:
        selection_label = f"{selection_label} {line_number}"
    return PlayTextInputs(
        away_team=away,
        home_team=home,
        market_label=market_label,
        selection_label=selection_label,
        odds_str=_format_american_odds(pick.line.odds if pick.line else None),
        grade=pick.grade or "C",
        read_notes=read_notes or "",
    )


def render_play(inputs: PlayTextInputs) -> str:
    """
    Render one play in the exact Phase 20 brand format. Four lines:

        {AWAY} @ {HOME} - {Market}
        Market Consensus: {Selection} ({Odds})
        EE Projection: Grade {Grade}
        Read: {Read notes}
    """
    matchup = f"{inputs.away_team} @ {inputs.home_team}".strip(" @")
    if not matchup:
        matchup = inputs.home_team or inputs.away_team or "Matchup"
    lines = [
        f"{matchup} - {inputs.market_label}",
    ]
    consensus_parts = [inputs.selection_label]
    if inputs.odds_str:
        consensus_parts.append(f"({inputs.odds_str})")
    lines.append(f"Market Consensus: {' '.join(consensus_parts)}")
    lines.append(f"EE Projection: Grade {inputs.grade}")
    read = inputs.read_notes or "No analytical delta recorded."
    lines.append(f"Read: {read}")
    return "\n".join(lines)


def render_plays(inputs_list: Iterable[PlayTextInputs]) -> str:
    """Blank-line-separated sequence of plays for a multi-pick card."""
    return "\n\n".join(render_play(i) for i in inputs_list)
