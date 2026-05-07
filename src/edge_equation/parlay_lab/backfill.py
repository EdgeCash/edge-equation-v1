"""Walk-forward backfill iterator.

Reads ``website/public/data/mlb/backtest.json``, groups its 121k+
graded bets by date, and yields one :class:`GradedSlate` per day. The
shootout passes each slate to every registered engine and grades the
recommended parlays against the recorded actuals.

Honest caveats baked in:

* The MLB backtest's bet rows don't store the historical American
  odds line. We derive ``decimal_odds`` from the ``units`` field
  on WIN rows (``units = decimal_odds - 1.0`` at 1u stake) and fall
  back to ``-110`` (decimal 1.909) on LOSS / PUSH. ROI comparisons
  between engines remain unbiased; absolute ROI under-counts winners
  on +money lines.

* Tier is reconstructed via ``classify_tier`` on each leg's
  ``model_prob`` + derived ``edge``. That mirrors what the live
  engine would have classified at pick time given the same prob
  + odds inputs.

* We don't filter by the strict gate yet --- engines decide what
  passes. The harness only supplies the candidate pool.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from edge_equation.engines.parlay.builder import ParlayLeg
from edge_equation.engines.tiering import Tier, classify_tier
from edge_equation.utils.kelly import american_to_decimal

from .base import GradedLeg, GradedSlate


# Default odds when we can't recover a real line from the backtest
# row (i.e. LOSS / PUSH with ``units == -1``). -110 is the standard
# MLB run-line / total juice.
_DEFAULT_DECIMAL_ODDS: float = american_to_decimal(-110.0)


@dataclass(frozen=True)
class BackfillSource:
    """Where a slate's rows came from. Carried on each :class:`GradedSlate`
    (for the leaderboard's provenance line) but not used by engines."""
    path: Path
    n_rows: int
    first_date: str
    last_date: str


def _decimal_odds_from_row(row: dict) -> float:
    """Recover decimal_odds from a backtest bet row.

    For WIN rows: ``units = decimal_odds - 1`` at 1u stake, so
    ``decimal_odds = units + 1.0``. For LOSS rows ``units == -1.0``
    always (the stake), and PUSH rows have ``units == 0.0``; both
    fall back to the default -110 line.
    """
    result = row.get("result")
    units = float(row.get("units") or 0.0)
    if result == "WIN" and units > 0:
        return units + 1.0
    return _DEFAULT_DECIMAL_ODDS


def _decimal_to_american(decimal_odds: float) -> float:
    """Inverse of ``american_to_decimal`` --- mirrors the helper in
    ``parlay.builder``. Inlined here to avoid a circular import."""
    if decimal_odds <= 1.0:
        return 0.0
    if decimal_odds >= 2.0:
        return (decimal_odds - 1.0) * 100.0
    return -100.0 / (decimal_odds - 1.0)


def _row_to_graded_leg(row: dict) -> Optional[GradedLeg]:
    """Convert one backtest bet row to a :class:`GradedLeg`.

    Returns None when the row is unusable (missing required fields).
    """
    bet_type = str(row.get("bet_type") or "")
    pick = str(row.get("pick") or "")
    matchup = str(row.get("matchup") or "")
    model_prob = row.get("model_prob")
    result = row.get("result")
    if not (bet_type and pick and matchup and result and model_prob is not None):
        return None
    try:
        prob = float(model_prob)
    except (TypeError, ValueError):
        return None
    if not (0.0 < prob < 1.0):
        return None

    decimal_odds = _decimal_odds_from_row(row)
    american = _decimal_to_american(decimal_odds)
    edge_frac = (decimal_odds * prob) - 1.0

    try:
        clf = classify_tier(
            market_type=bet_type,
            edge=edge_frac,
            side_probability=prob,
        )
        tier = clf.tier
    except Exception:
        tier = Tier.NO_PLAY

    leg = ParlayLeg(
        market_type=bet_type,
        side=pick,
        side_probability=prob,
        american_odds=american,
        tier=tier,
        game_id=matchup,
        player_id=None,
        label=f"{matchup} {bet_type} {pick}",
    )
    pick_id = f"{row.get('date')}|{matchup}|{bet_type}|{pick}"
    return GradedLeg(
        leg=leg,
        result=str(result),
        decimal_odds=decimal_odds,
        pick_id=pick_id,
    )


def load_backfill(
    path: str | Path,
    *,
    after_date: Optional[str] = None,
    before_date: Optional[str] = None,
    bet_types: Optional[set[str]] = None,
    min_tier: Tier = Tier.LEAN,
) -> tuple[BackfillSource, list[GradedSlate]]:
    """Load + group backtest bets into per-day :class:`GradedSlate`s.

    Filters:
      * ``after_date`` / ``before_date`` --- inclusive ISO bounds.
      * ``bet_types`` --- optional whitelist (e.g. ``{"moneyline",
        "totals"}``).
      * ``min_tier`` --- legs below this tier are excluded from the
        slate. Defaults to LEAN (everything ledger-eligible).

    Returns the provenance + a list of slates sorted by date asc.
    """
    p = Path(path)
    with p.open() as fh:
        data = json.load(fh)
    rows = list(data.get("bets") or [])

    by_date: dict[str, list[GradedLeg]] = defaultdict(list)
    for row in rows:
        date = str(row.get("date") or "")
        if not date:
            continue
        if after_date and date < after_date:
            continue
        if before_date and date > before_date:
            continue
        if bet_types and row.get("bet_type") not in bet_types:
            continue
        graded = _row_to_graded_leg(row)
        if graded is None:
            continue
        if graded.leg.tier == Tier.NO_PLAY:
            continue
        # Ordering of the Tier enum matters: classify_tier returns
        # values from a fixed ladder; compare via the documented
        # ranking helper instead of < (Tier doesn't define ordering).
        if not _tier_ge(graded.leg.tier, min_tier):
            continue
        by_date[date].append(graded)

    dates_sorted = sorted(by_date.keys())
    slates = [
        GradedSlate(date=d, graded_legs=tuple(by_date[d]))
        for d in dates_sorted
    ]

    source = BackfillSource(
        path=p,
        n_rows=sum(len(s.graded_legs) for s in slates),
        first_date=dates_sorted[0] if dates_sorted else "",
        last_date=dates_sorted[-1] if dates_sorted else "",
    )
    return source, slates


_TIER_RANK: dict[Tier, int] = {
    Tier.NO_PLAY: 0,
    Tier.LEAN: 1,
    Tier.MODERATE: 2,
    Tier.STRONG: 3,
    Tier.ELITE: 4,
}


def _tier_ge(a: Tier, b: Tier) -> bool:
    return _TIER_RANK.get(a, 0) >= _TIER_RANK.get(b, 0)


def iter_slates(
    slates: Iterable[GradedSlate],
    *,
    min_legs_per_slate: int = 2,
) -> Iterator[GradedSlate]:
    """Yield slates that meet a minimum leg-count threshold.

    Days with fewer than ``min_legs_per_slate`` qualifying legs are
    skipped (a parlay needs at least 2 legs --- a 1-leg slate has
    nothing to combine).
    """
    for slate in slates:
        if len(slate.graded_legs) >= min_legs_per_slate:
            yield slate
