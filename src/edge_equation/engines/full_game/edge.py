"""Edge computation + qualifying-pick selection for full-game markets.

Per the audit: full-game markets use the **edge ladder**
(model_p − vig-adjusted market_p), not raw probability. A 60%
prediction on a -150 favourite is a fade, not a play.

Pipeline:

1. Pair Over/Under (or Home-side/Away-side) outcomes on the same
   (event, market, line) to compute the de-vig sum.
2. Edge = `model_prob − devigged_market_prob`.
3. Classify via `engines.tiering.classify_tier(edge=...)`.
4. Filter by `min_tier` (LEAN by default — same threshold the props
   engine uses).

For the **moneyline** the pair is home-side / away-side; we key on
(event, market, line=NULL) and let the existing pair-summing logic
handle it. Run_Line same idea — the spread comes in two complementary
lines (-1.5 / +1.5) but only when the book posts both; some books
post just the favourite, in which case the pair lookup is missing
and we fall back to raw implied probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from edge_equation.engines.tiering import Tier, TierClassification, classify_tier
from edge_equation.utils.kelly import implied_probability

from .odds_fetcher import FullGameLine
from .projection import ProjectedFullGameSide


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FullGameEdgePick:
    """One qualifying side, ready to surface."""
    market_canonical: str
    market_label: str
    home_team: str
    away_team: str
    home_tricode: str
    away_tricode: str
    side: str
    team_tricode: str
    line_value: Optional[float]
    model_prob: float
    market_prob_raw: float
    market_prob_devigged: float
    vig_corrected: bool
    edge_pp: float
    american_odds: float
    decimal_odds: float
    book: str
    tier: Tier
    tier_classification: TierClassification


# ---------------------------------------------------------------------------
# Vig adjustment
# ---------------------------------------------------------------------------


def _key_for_pair(line: FullGameLine) -> tuple:
    """De-vig pair key: (event, market, line). For ML where line_value
    is None we still pair Home vs Away on (event, market, None)."""
    return (line.event_id, line.market.canonical,
            float(line.line_value) if line.line_value is not None else None)


def build_devig_table(
    lines: Iterable[FullGameLine],
) -> dict[tuple, float]:
    """Return {pair_key → sum-of-implied-probs} for each pair we saw."""
    table: dict[tuple, list[FullGameLine]] = {}
    for line in lines:
        table.setdefault(_key_for_pair(line), []).append(line)
    out: dict[tuple, float] = {}
    for key, sides in table.items():
        if len(sides) < 2:
            continue
        total = sum(implied_probability(s.american_odds) for s in sides)
        if total <= 0:
            continue
        out[key] = total
    return out


# ---------------------------------------------------------------------------
# Edge math
# ---------------------------------------------------------------------------


def compute_edge_pp(
    *, line: FullGameLine, projection: ProjectedFullGameSide,
    devig_total: Optional[float] = None,
) -> tuple[float, float, float, bool]:
    """Return (edge_pp, market_prob_raw, market_prob_devigged, corrected)."""
    raw = implied_probability(line.american_odds)
    if devig_total is None or devig_total <= 0:
        devigged = raw
        corrected = False
    else:
        devigged = raw / devig_total
        corrected = True
    edge = (projection.model_prob - devigged) * 100.0
    return float(edge), float(raw), float(devigged), bool(corrected)


# ---------------------------------------------------------------------------
# Pick assembly
# ---------------------------------------------------------------------------


def build_edge_picks(
    lines: Sequence[FullGameLine],
    projections: Sequence[ProjectedFullGameSide],
    *, min_tier: Tier = Tier.LEAN,
    min_confidence: float = 0.31,
) -> list[FullGameEdgePick]:
    """Pair each (line, projection), compute edge, classify, filter.

    Default ``min_tier=Tier.LEAN`` keeps the ledger-eligible threshold;
    operators wanting public-facing only pass ``Tier.STRONG`` to drop
    LEAN/MODERATE.

    ``min_confidence`` excludes projections that rest entirely on the
    league prior (every team is league-average → ``confidence == 0.30``
    via ``_confidence_for_blend(min_n=0, …)``). The
    ``default_team_rates_table()`` seed used on day one returns
    ``n_games=0`` for every tricode, so without this floor every game
    on the slate would project as a coin-flip vs the per-team market
    line and produce inflated edges. Backtest callers that explicitly
    want the trivial baseline can pass ``min_confidence=0.0``.
    """
    if len(lines) != len(projections):
        raise ValueError(
            f"lines / projections length mismatch: "
            f"{len(lines)} vs {len(projections)}",
        )
    devig = build_devig_table(lines)
    picks: list[FullGameEdgePick] = []
    rank = {Tier.ELITE: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
              Tier.LEAN: 1, Tier.NO_PLAY: 0}
    floor = rank[min_tier]
    for line, proj in zip(lines, projections):
        if float(proj.confidence) < min_confidence:
            continue
        edge_pp, raw, devigged, corrected = compute_edge_pp(
            line=line, projection=proj,
            devig_total=devig.get(_key_for_pair(line)),
        )
        clf = classify_tier(market_type=line.market.canonical,
                              edge=edge_pp / 100.0)
        if rank[clf.tier] < floor:
            continue
        picks.append(FullGameEdgePick(
            market_canonical=line.market.canonical,
            market_label=line.market.label,
            home_team=line.home_team, away_team=line.away_team,
            home_tricode=line.home_tricode, away_tricode=line.away_tricode,
            side=line.side, team_tricode=line.team_tricode,
            line_value=line.line_value,
            model_prob=proj.model_prob,
            market_prob_raw=raw, market_prob_devigged=devigged,
            vig_corrected=corrected, edge_pp=edge_pp,
            american_odds=line.american_odds,
            decimal_odds=line.decimal_odds, book=line.book,
            tier=clf.tier, tier_classification=clf,
        ))
    picks.sort(key=lambda p: p.edge_pp, reverse=True)
    return picks
