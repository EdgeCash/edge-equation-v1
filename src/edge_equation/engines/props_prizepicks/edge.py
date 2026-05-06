"""Edge computation + qualifying-pick selection for player props.

Per the engine's audit-locked policy: props use the **edge ladder**
(model_p − vig-adjusted market_p), not the raw-probability ladder
(NRFI's symmetric framework only applies to ~50/50 markets like
NRFI/YRFI). Tier classification calls out to `engines.tiering` so
there's one grading system across the entire engine.

Pipeline
--------

1. Fetch market lines with `odds_fetcher.fetch_all_player_props`.
2. Project each side with `projection.project_all`.
3. For each (line, projection) pair, compute the edge:
     edge = model_prob − vig_adjusted_market_prob
4. Classify the tier off `edge` (ELITE ≥ 8pp, STRONG 5–8pp, ...).
5. Filter out non-qualifying sides.

Vig handling
------------

The book's posted American odds carry vig. Naive implied probability
overstates the book's true price; we de-vig by pairing each Over /
Under outcome on the same (player, market, line) and renormalising so
the pair sums to 1.0. When the under side is missing (some books
post Over-only HR yes/no markets), we fall back to the raw implied
probability and flag the row with a `vig_corrected=False` marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from edge_equation.engines.tiering import Tier, TierClassification, classify_tier
from edge_equation.utils.kelly import implied_probability

from .odds_fetcher import PlayerPropLine
from .projection import ProjectedSide


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropEdgePick:
    """One qualifying side, ready to surface in a daily report or ledger.

    Mirrors the fields the email / dashboard renderers already know how
    to display so the props engine drops into existing surfaces without
    a new adapter.
    """
    market_canonical: str
    market_label: str
    player_name: str
    line_value: float
    side: str
    model_prob: float
    market_prob_raw: float            # implied prob from American odds, vigged
    market_prob_devigged: float       # vig-adjusted (= raw if pair missing)
    vig_corrected: bool
    edge_pp: float                    # signed pp; positive = overlay
    american_odds: float
    decimal_odds: float
    book: str
    tier: Tier
    tier_classification: TierClassification
    # Calibration provenance -- raw_model_prob is the un-shrunk Poisson
    # output, model_prob is the post-shrink prob the gate evaluated.
    # Lets the dashboard show "we projected X, calibrated to Y."
    raw_model_prob: float = 0.0


# ---------------------------------------------------------------------------
# Vig adjustment
# ---------------------------------------------------------------------------


def _key_for_pair(line: PlayerPropLine) -> tuple:
    return (line.event_id, line.market.canonical, line.player_name,
            float(line.line_value))


def build_devig_table(
    lines: Iterable[PlayerPropLine],
) -> dict[tuple, float]:
    """Pair Over/Under sides and return the renormalising sum per pair.

    Output: {(event_id, market, player, line) → over+under_implied_total}.
    Caller divides each side's implied prob by this total to de-vig.
    Pairs that only have one side are absent from the dict — caller
    should fall back to raw implied probability for those.
    """
    table: dict[tuple, list[PlayerPropLine]] = {}
    for line in lines:
        table.setdefault(_key_for_pair(line), []).append(line)
    out: dict[tuple, float] = {}
    for key, sides in table.items():
        if len(sides) < 2:
            continue
        # Sum implied probs across all sides on this pair (typically
        # exactly two: Over + Under). Books occasionally post a third
        # "push" outcome on integer lines; summing is still correct.
        total = sum(implied_probability(s.american_odds) for s in sides)
        if total <= 0:
            continue
        out[key] = total
    return out


# ---------------------------------------------------------------------------
# Edge computation
# ---------------------------------------------------------------------------


def compute_edge_pp(
    *,
    line: PlayerPropLine,
    projection: ProjectedSide,
    devig_total: Optional[float] = None,
) -> tuple[float, float, float, bool]:
    """Return ``(edge_pp, market_prob_raw, market_prob_devigged, vig_corrected)``.

    `edge_pp` is in percentage points: 5.1 means model is 5.1pp more
    confident in the side hitting than the book is.
    """
    raw = implied_probability(line.american_odds)
    if devig_total is None or devig_total <= 0:
        devigged = raw
        corrected = False
    else:
        devigged = raw / devig_total
        corrected = True
    edge = (projection.model_prob - devigged) * 100.0
    return float(edge), float(raw), float(devigged), bool(corrected)


def build_edge_picks(
    lines: Sequence[PlayerPropLine],
    projections: Sequence[ProjectedSide],
    *,
    min_tier: Tier = Tier.LEAN,
    min_confidence: float = 0.31,
    apply_calibration: bool = True,
    calibration_temperature: Optional[dict[str, float]] = None,
    min_model_prob: float = 0.0,
    min_edge_pp: float = 0.0,
) -> list[PropEdgePick]:
    """Pair each (line, projection), compute edge, classify tier, filter.

    Default `min_tier=Tier.LEAN` keeps the ledger-eligible threshold
    (`is_qualifying`) — operators who want the public-facing report
    can pass `Tier.STRONG` to drop LEAN/MODERATE.

    `min_confidence` excludes projections that rest entirely on the
    league prior (`blend_n == 0` → ``confidence == 0.30``). When the
    Statcast loader can't find per-player rates, every pitcher gets
    the same league-average λ, and the market prices each pitcher
    individually — that mismatch produces fake "edges" of 20-30pp on
    every line, classifying everyone as ELITE. The default floor of
    ``0.31`` (slightly above the pure-prior baseline) keeps day-one
    operations honest. Backtest callers that want the trivial
    baseline can pass ``min_confidence=0.0``.
    """
    if len(lines) != len(projections):
        raise ValueError(
            f"lines / projections length mismatch: "
            f"{len(lines)} vs {len(projections)}",
        )
    from .projection import calibrate_prob as _calibrate_prob
    devig = build_devig_table(lines)
    picks: list[PropEdgePick] = []
    rank = {Tier.ELITE: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
              Tier.LEAN: 1, Tier.NO_PLAY: 0}
    floor = rank[min_tier]
    for line, proj in zip(lines, projections):
        if float(proj.confidence) < min_confidence:
            continue
        # First-pass de-vig + raw edge so we know the market's view.
        _raw_edge, raw, devigged, corrected = compute_edge_pp(
            line=line, projection=proj,
            devig_total=devig.get(_key_for_pair(line)),
        )
        raw_prob = float(proj.model_prob)
        # Apply the calibration shrink toward the de-vigged market
        # price. This is the props analogue of the MLB temperature
        # shrink toward 0.5: a Bayesian blend that penalises Poisson's
        # light-tail over-confidence without distorting the picks the
        # model is in actual disagreement with the book about.
        if apply_calibration:
            cal_prob = _calibrate_prob(
                model_prob=raw_prob,
                market_prob_devigged=devigged,
                market_canonical=line.market.canonical,
                temperature=calibration_temperature,
            )
        else:
            cal_prob = raw_prob
        # Re-compute edge using the calibrated prob so tier + Premium
        # filters all see one consistent number.
        edge_pp = (cal_prob - devigged) * 100.0

        # Premium-style PLAY filters layered on top of the existing
        # edge ladder. min_model_prob screens out "fade" picks
        # (e.g. Under 0.5 RBIs at 38% calibrated -- big edge by accident
        # of vig but no real conviction). min_edge_pp gives the caller
        # a single floor that combines well with the tier ladder.
        if cal_prob < min_model_prob:
            continue
        if edge_pp < min_edge_pp:
            continue

        # Pass calibrated model prob to the tiering ELITE floor
        # (model_prob >= 0.62) so the calibrated result drives the
        # tier promotion -- not the raw Poisson over-confidence.
        clf = classify_tier(
            market_type=line.market.canonical,
            edge=edge_pp / 100.0,
            side_probability=float(cal_prob),
        )
        if rank[clf.tier] < floor:
            continue
        picks.append(PropEdgePick(
            market_canonical=line.market.canonical,
            market_label=line.market.label,
            player_name=line.player_name,
            line_value=line.line_value,
            side=line.side,
            model_prob=float(cal_prob),
            market_prob_raw=raw,
            market_prob_devigged=devigged,
            vig_corrected=corrected,
            edge_pp=float(edge_pp),
            american_odds=line.american_odds,
            decimal_odds=line.decimal_odds,
            book=line.book,
            tier=clf.tier,
            tier_classification=clf,
            raw_model_prob=raw_prob,
        ))
    picks.sort(key=lambda p: p.edge_pp, reverse=True)
    return picks
