"""Prop-specific backfill from PrizePicks snapshots.

Reads the hourly PrizePicks scrapes under ``data/prizepicks/snapshots/``
and assembles a per-day :class:`GradedSlate` of MLB prop legs. The
**lines are real** (player + market + line + odds_type came straight
off PrizePicks); the **model_prob and outcomes are synthesized** via
a parametric model calibrated to typical PrizePicks juice.

Why synthetic: the production engine doesn't yet have a long-running
graded-props ledger we can read from a checked-in file. Until it does,
we generate the missing pieces deterministically (seeded RNG) so the
shootout produces reproducible numbers. The framework swaps in real
outcomes when the props ledger accumulates --- callers just point
:func:`load_props_backfill` at a different source.

What's modelled:

* **odds_type buckets** (demon / goblin / standard) drive both the
  base hit-rate and the model_prob distribution. Demons are the harder
  side: lower hit rate (~0.35), engine projects them lower
  (mean ~0.40). Goblins are the easier side: higher hit rate (~0.65),
  engine projects higher (mean ~0.60). Standards sit at ~0.50 both
  ways.

* **Same-player correlation** via a per-(date, player) "performance
  state" Bernoulli. Two props on Aaron Judge tend to both hit or both
  miss --- exactly the structure we believe the live engine has been
  ignoring. This means engines that de-dup or diversify by player
  should outperform pure baseline on this backfill.

* **Same-game weak correlation** is left out for v1 --- can be layered
  in later when we have real ledger data to calibrate against.

Engines compare apples-to-apples because every engine sees the same
synthesized model_prob + outcome. Absolute ROI is simulated; the
relative leaderboard is the signal.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from edge_equation.engines.parlay.builder import ParlayLeg
from edge_equation.engines.tiering import Tier, classify_tier
from edge_equation.utils.kelly import american_to_decimal

from .backfill import BackfillSource
from .base import GradedLeg, GradedSlate


# Decimal odds at the fallback PrizePicks juice (-110 implicit per leg).
_DEFAULT_DECIMAL_ODDS: float = american_to_decimal(-110.0)


# PrizePicks stat_type -> the parlay engine's market_type slug. Anything
# not in this map is dropped (composite stats like "Hits+Runs+RBIs" or
# in-game live props don't fit the strict-gate model).
_STAT_TYPE_TO_MARKET: dict[str, str] = {
    "Hits": "Hits",
    "Total Bases": "Total_Bases",
    "Home Runs": "HR",
    "Hits Allowed": "Hits_Allowed",
    "Pitcher Strikeouts": "K",
    "Strikeouts": "K",
    "Pitches Thrown": "Pitches",
    "Runs": "Runs",
    "RBIs": "RBI",
    "Stolen Bases": "SB",
    "Walks": "Walks",
    "Earned Runs Allowed": "Earned_Runs",
    "Singles": "Singles",
    "Doubles": "Doubles",
    "Triples": "Triples",
    "Outs": "Outs",
}


@dataclass(frozen=True)
class PropSyntheticConfig:
    """Tunable parameters for the synthetic outcome + model_prob model.

    All values calibrated against PrizePicks' published "no-juice
    multiplier" tables --- demons pay more because they hit less,
    goblins pay less because they hit more. Default values are
    conservative midpoints; bump them via the constructor when you
    want to stress-test under different conditions.
    """

    # Mean + std of the engine's projected probability (model_prob)
    # for each odds_type. Sampled per-leg from a Beta with these moments.
    demon_model_prob_mean: float = 0.40
    demon_model_prob_std: float = 0.07
    goblin_model_prob_mean: float = 0.60
    goblin_model_prob_std: float = 0.07
    standard_model_prob_mean: float = 0.50
    standard_model_prob_std: float = 0.07

    # Per-prop hit rates conditioned on the player's performance state.
    # E[hit_rate] = p_good_day * good_rate + (1 - p_good_day) * bad_rate.
    # Defaults give: demon ~0.40, goblin ~0.60, standard ~0.50 ---
    # consistent with PrizePicks long-run hit rates per odds_type.
    p_good_day: float = 0.50
    demon_p_good: float = 0.55
    demon_p_bad: float = 0.25
    goblin_p_good: float = 0.75
    goblin_p_bad: float = 0.45
    standard_p_good: float = 0.60
    standard_p_bad: float = 0.40

    decimal_odds: float = _DEFAULT_DECIMAL_ODDS

    # Hard cap on legs per slate after dedup --- a typical PrizePicks
    # MLB scrape has 1k+ rows. The engine in production qualifies maybe
    # 30-100 LEAN+. Sample down deterministically.
    max_legs_per_date: int = 80

    # Seed for both the model_prob sampler and the outcome simulator.
    # Per-(date, projection_id) hashes are mixed in so individual legs
    # decorrelate while overall draws stay reproducible.
    seed: int = 42


def _stable_rng(seed: int, *parts: str) -> random.Random:
    """Deterministic per-leg RNG seeded by the global seed + a stable
    hash of the leg's identity. Lets two runs with the same seed
    produce identical synthetic outcomes."""
    h = hashlib.md5(("|".join(parts) + f"|{seed}").encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _beta_sample(rng: random.Random, mean: float, std: float) -> float:
    """Sample from Beta(α, β) with the requested mean + std. Returns a
    value strictly inside (0.001, 0.999) so downstream log/odds math
    never blows up on a 0 or 1."""
    mean = min(0.999, max(0.001, mean))
    var = std * std
    # Method-of-moments: alpha + beta = mean*(1-mean)/var - 1.
    denom = max(1e-6, mean * (1.0 - mean) - var)
    s = max(2.0, denom / max(1e-6, var))
    alpha = max(0.5, mean * s)
    beta = max(0.5, (1.0 - mean) * s)
    x = rng.betavariate(alpha, beta)
    return min(0.999, max(0.001, x))


def _row_decimal_odds(row: dict, config: PropSyntheticConfig) -> float:
    """Effective per-leg decimal odds keyed off ``odds_type``.

    PrizePicks doesn't publish per-leg American odds (their payout is
    a multiplier on the slip size), but we synthesize per-leg odds at
    the implied prob of each odds_type's *mean* model_prob:

        demon    -> decimal = 1 / 0.40 = 2.50  (priced for 40% hit)
        goblin   -> decimal = 1 / 0.60 = 1.67  (priced for 60% hit)
        standard -> decimal = 1 / 0.50 = 2.00  (priced for 50% hit)

    Pricing each bucket fair on average means the +EV legs the engines
    pick are the ones whose sampled model_prob beats the bucket's
    baseline --- a realistic stress test (about half of each bucket
    has positive edge). Without this, demons priced at -110 would all
    project negative-edge and get filtered before they reach any
    engine.
    """
    odds_type = (row.get("odds_type") or "standard").strip().lower()
    if odds_type == "demon":
        return 1.0 / max(0.001, config.demon_model_prob_mean)
    if odds_type == "goblin":
        return 1.0 / max(0.001, config.goblin_model_prob_mean)
    return 1.0 / max(0.001, config.standard_model_prob_mean)


def _model_prob_for_row(
    row: dict, *, config: PropSyntheticConfig, date: str,
) -> float:
    odds_type = (row.get("odds_type") or "standard").strip().lower()
    if odds_type == "demon":
        mean, std = config.demon_model_prob_mean, config.demon_model_prob_std
    elif odds_type == "goblin":
        mean, std = config.goblin_model_prob_mean, config.goblin_model_prob_std
    else:
        mean, std = (
            config.standard_model_prob_mean, config.standard_model_prob_std,
        )
    rng = _stable_rng(
        config.seed,
        "model_prob", date, row.get("projection_id") or "",
        row.get("player") or "", row.get("stat_type") or "",
    )
    return _beta_sample(rng, mean, std)


def _outcome_for_row(
    row: dict, *, config: PropSyntheticConfig, date: str,
    player_states: dict[tuple[str, str], bool],
) -> str:
    """Generate WIN / LOSS for one prop, conditioned on the player's
    per-day state. Pushes are rare in PrizePicks (0.5 / 1.5 / 2.5 lines
    avoid integer ties); we don't model them in v1."""
    odds_type = (row.get("odds_type") or "standard").strip().lower()
    player = row.get("player") or ""
    is_good_day = player_states.setdefault(
        (date, player),
        _stable_rng(
            config.seed, "player_state", date, player,
        ).random() < config.p_good_day,
    )
    if odds_type == "demon":
        p_win = config.demon_p_good if is_good_day else config.demon_p_bad
    elif odds_type == "goblin":
        p_win = config.goblin_p_good if is_good_day else config.goblin_p_bad
    else:
        p_win = config.standard_p_good if is_good_day else config.standard_p_bad
    rng = _stable_rng(
        config.seed,
        "outcome", date, row.get("projection_id") or "",
        row.get("player") or "", row.get("stat_type") or "",
    )
    return "WIN" if rng.random() < p_win else "LOSS"


def _row_to_graded_leg(
    row: dict, *, date: str,
    config: PropSyntheticConfig,
    player_states: dict[tuple[str, str], bool],
) -> Optional[GradedLeg]:
    stat_type = (row.get("stat_type") or "").strip()
    market_type = _STAT_TYPE_TO_MARKET.get(stat_type)
    if market_type is None:
        return None
    line_raw = row.get("line")
    try:
        line_num = float(line_raw)
    except (TypeError, ValueError):
        return None
    player = (row.get("player") or "").strip()
    if not player:
        return None
    odds_type = (row.get("odds_type") or "standard").strip().lower()
    side = "Over"  # PrizePicks displays the over side; demons just elevate the line
    side_str = f"{side} {line_num:g}"

    decimal_odds = _row_decimal_odds(row, config)
    american = _decimal_to_american(decimal_odds)
    model_prob = _model_prob_for_row(row, config=config, date=date)
    edge_frac = (decimal_odds * model_prob) - 1.0

    try:
        clf = classify_tier(
            market_type=market_type, edge=edge_frac, side_probability=model_prob,
        )
        tier = clf.tier
    except Exception:
        tier = Tier.NO_PLAY

    proj_id = str(row.get("projection_id") or "")
    pid = f"{date}|{player}|{stat_type}|{line_num:g}|{odds_type}"
    leg = ParlayLeg(
        market_type=market_type,
        side=side_str,
        side_probability=model_prob,
        american_odds=american,
        tier=tier,
        game_id=str(row.get("description") or ""),
        player_id=f"prop:{player}",
        label=f"{player} {market_type} {side_str} ({odds_type})",
    )
    result = _outcome_for_row(
        row, config=config, date=date, player_states=player_states,
    )
    return GradedLeg(
        leg=leg, result=result, decimal_odds=decimal_odds, pick_id=pid,
    )


def _decimal_to_american(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        return 0.0
    if decimal_odds >= 2.0:
        return (decimal_odds - 1.0) * 100.0
    return -100.0 / (decimal_odds - 1.0)


def _date_from_filename(path: Path) -> Optional[str]:
    """Snapshot files are named ``prizepicks_<ISO timestamp>.csv``; the
    first 10 chars of the timestamp are the YYYY-MM-DD."""
    stem = path.stem
    if not stem.startswith("prizepicks_"):
        return None
    return stem[len("prizepicks_"):][:10]


def _first_snapshot_per_date(snapshots_dir: Path) -> dict[str, Path]:
    """One snapshot per date --- prefer the earliest scrape so we get
    pre-game lines, not in-game-adjusted ones. Snapshots earlier than
    the slate's MLB games avoid game-state contamination."""
    by_date: dict[str, Path] = {}
    for f in sorted(snapshots_dir.glob("prizepicks_*.csv")):
        d = _date_from_filename(f)
        if d is None:
            continue
        if d not in by_date:
            by_date[d] = f
    return by_date


def _stream_mlb_rows(
    path: Path, *, league: str = "MLB", projection_seen: set[str],
    stat_types: Optional[set[str]] = None,
) -> Iterator[dict]:
    """Yield rows from a snapshot, filtered to a single league + first
    occurrence of each projection_id. The CSVs are large (500k+ rows
    across all sports) so streaming + early-skipping matters."""
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("league") != league:
                continue
            pid = row.get("projection_id") or ""
            if not pid or pid in projection_seen:
                continue
            if stat_types and row.get("stat_type") not in stat_types:
                continue
            projection_seen.add(pid)
            yield row


def load_props_backfill(
    snapshots_dir: str | Path,
    *,
    after_date: Optional[str] = None,
    before_date: Optional[str] = None,
    league: str = "MLB",
    config: Optional[PropSyntheticConfig] = None,
    stat_types: Optional[set[str]] = None,
    min_tier: Tier = Tier.LEAN,
) -> tuple[BackfillSource, list[GradedSlate]]:
    """Build per-day :class:`GradedSlate`s from PrizePicks snapshots.

    Each date's slate is downsampled to ``config.max_legs_per_date``
    legs (deterministic, seeded). The synthetic model_prob + outcome
    are generated inline; same-player correlation is injected via a
    per-(date, player) good/bad-day state.
    """
    cfg = config or PropSyntheticConfig()
    snapshots_dir = Path(snapshots_dir)
    if not snapshots_dir.exists():
        raise FileNotFoundError(f"snapshots dir not found: {snapshots_dir}")

    by_date_path = _first_snapshot_per_date(snapshots_dir)
    dates = sorted(by_date_path.keys())
    if after_date:
        dates = [d for d in dates if d >= after_date]
    if before_date:
        dates = [d for d in dates if d <= before_date]
    if not dates:
        return _empty_source(snapshots_dir), []

    slates: list[GradedSlate] = []
    total_rows = 0
    for date in dates:
        path = by_date_path[date]
        seen_projections: set[str] = set()
        graded: list[GradedLeg] = []
        player_states: dict[tuple[str, str], bool] = {}
        # Stream + dedupe + filter + grade
        for row in _stream_mlb_rows(
            path, league=league, projection_seen=seen_projections,
            stat_types=stat_types,
        ):
            g = _row_to_graded_leg(
                row, date=date, config=cfg, player_states=player_states,
            )
            if g is None:
                continue
            if not _tier_ge(g.leg.tier, min_tier):
                continue
            graded.append(g)
        if not graded:
            continue
        # Deterministic downsample: sort by stable id, take top N.
        graded.sort(key=lambda g: g.pick_id)
        rng = _stable_rng(cfg.seed, "downsample", date)
        if len(graded) > cfg.max_legs_per_date:
            rng.shuffle(graded)
            graded = graded[: cfg.max_legs_per_date]
        slates.append(GradedSlate(date=date, graded_legs=tuple(graded)))
        total_rows += len(graded)

    source = BackfillSource(
        path=snapshots_dir,
        n_rows=total_rows,
        first_date=slates[0].date if slates else "",
        last_date=slates[-1].date if slates else "",
    )
    return source, slates


def _empty_source(path: Path) -> BackfillSource:
    return BackfillSource(path=path, n_rows=0, first_date="", last_date="")


_TIER_RANK: dict[Tier, int] = {
    Tier.NO_PLAY: 0, Tier.LEAN: 1, Tier.MODERATE: 2,
    Tier.STRONG: 3, Tier.ELITE: 4,
}


def _tier_ge(a: Tier, b: Tier) -> bool:
    return _TIER_RANK.get(a, 0) >= _TIER_RANK.get(b, 0)
