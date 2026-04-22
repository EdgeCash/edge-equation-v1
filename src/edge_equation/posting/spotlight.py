"""
Spotlight selector.

The 4pm CT Spotlight is a single-game deep-dive card. We don't have live
social-trend signals or public-money numbers available deterministically,
so the "most-trending game" is chosen from the engine's own output:

    trend_score(game) = sum over its picks of
        grade_weight(pick.grade) * abs(pick.edge) * (1 + pick.kelly)

with a hard requirement that the game has at least one Grade A or A+
pick (same bar as Daily Edge). Ties break on:
  1. Higher grade_weight of the best pick on the game.
  2. Higher abs(edge) of the best pick.
  3. Sport priority (US majors > international).
  4. Lexicographic game_id.

All inputs are deterministic and already in the Pick, so the selector
produces identical output for identical input. That matters because the
Spotlight gets replayed during audits.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from edge_equation.engine.pick_schema import Pick


# Grade -> numeric weight. A+ carries more trend mass than A; anything
# below Grade A is ignored for Spotlight selection (a B/C trend read
# would be beneath the brand bar).
_GRADE_WEIGHT = {"A+": Decimal("1.25"), "A": Decimal("1.00")}

_SPORT_PRIORITY = {
    "MLB": 10, "NFL": 10, "NBA": 10, "NHL": 10,
    "KBO": 5,  "NPB": 5,  "Soccer": 5,
}


@dataclass(frozen=True)
class SpotlightSelection:
    """Result of the Spotlight selector: picks belonging to the trending
    game, plus a numeric trend_score for audit logs. Empty picks means
    nothing qualified; the caller should emit the 'no Spotlight today'
    short card."""
    game_id: Optional[str]
    picks: Tuple[Pick, ...]
    trend_score: Decimal
    sport: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "sport": self.sport,
            "trend_score": str(self.trend_score),
            "n_picks": len(self.picks),
        }


def _pick_contribution(pick: Pick) -> Decimal:
    weight = _GRADE_WEIGHT.get(pick.grade or "")
    if weight is None:
        return Decimal("0")
    edge = pick.edge if pick.edge is not None else Decimal("0")
    kelly = pick.kelly if pick.kelly is not None else Decimal("0")
    # abs(edge) so a short (negative-edge) pick would never trend-rank;
    # but we've already filtered on Grade A / A+ which implies edge>=0.05.
    abs_edge = edge if edge >= 0 else -edge
    kelly_boost = Decimal("1") + (kelly if kelly > 0 else Decimal("0"))
    return weight * abs_edge * kelly_boost


def _best_grade_weight(picks: Sequence[Pick]) -> Decimal:
    return max(
        (_GRADE_WEIGHT.get(p.grade or "", Decimal("0")) for p in picks),
        default=Decimal("0"),
    )


def _best_abs_edge(picks: Sequence[Pick]) -> Decimal:
    best = Decimal("0")
    for p in picks:
        e = p.edge if p.edge is not None else Decimal("0")
        if e < 0:
            e = -e
        if e > best:
            best = e
    return best


def select_spotlight_game(picks: Sequence[Pick]) -> SpotlightSelection:
    """
    Pick the single game whose trend_score is highest among games that
    have at least one Grade A or A+ pick. Returns an empty selection if
    nothing qualifies.
    """
    if not picks:
        return SpotlightSelection(game_id=None, picks=(), trend_score=Decimal("0"))

    by_game: Dict[str, List[Pick]] = {}
    for p in picks:
        key = p.game_id or ""
        if not key:
            continue
        by_game.setdefault(key, []).append(p)

    scored: List[Tuple[str, List[Pick], Decimal, Decimal, Decimal, int, str]] = []
    for game_id, game_picks in by_game.items():
        if not any((p.grade or "") in _GRADE_WEIGHT for p in game_picks):
            continue
        trend = sum((_pick_contribution(p) for p in game_picks), Decimal("0"))
        best_grade_w = _best_grade_weight(game_picks)
        best_edge = _best_abs_edge(game_picks)
        sport = game_picks[0].sport or ""
        sport_prio = _SPORT_PRIORITY.get(sport, 0)
        scored.append(
            (game_id, game_picks, trend, best_grade_w, best_edge, sport_prio, game_id)
        )

    if not scored:
        return SpotlightSelection(game_id=None, picks=(), trend_score=Decimal("0"))

    # Sort by (trend desc, best_grade_w desc, best_edge desc, sport_prio desc, game_id asc)
    scored.sort(key=lambda t: (-t[2], -t[3], -t[4], -t[5], t[6]))
    top = scored[0]
    return SpotlightSelection(
        game_id=top[0],
        picks=tuple(top[1]),
        trend_score=top[2],
        sport=top[1][0].sport if top[1] else None,
    )
