"""Calibration & profitability metrics for the NRFI/YRFI engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from ..utils.kelly import american_to_decimal


@dataclass
class CalibrationReport:
    n: int
    brier: float
    log_loss: float
    accuracy: float
    base_rate: float
    bucket_edges: list[float]
    bucket_pred_mean: list[float]
    bucket_actual: list[float]
    bucket_count: list[int]


def brier_score(p: Sequence[float], y: Sequence[int]) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    return float(np.mean((p - y) ** 2))


def log_loss_score(p: Sequence[float], y: Sequence[int], eps: float = 1e-9) -> float:
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    y = np.asarray(y, dtype=int)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def accuracy(p: Sequence[float], y: Sequence[int], threshold: float = 0.5) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    return float(((p >= threshold).astype(int) == y).mean())


def reliability_buckets(p: Sequence[float], y: Sequence[int],
                         n_bins: int = 10) -> CalibrationReport:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    pred_mean, actual, count = [], [], []
    for i in range(n_bins):
        mask = (p >= edges[i]) & (p < edges[i + 1] if i < n_bins - 1 else p <= edges[i + 1])
        if mask.any():
            pred_mean.append(float(p[mask].mean()))
            actual.append(float(y[mask].mean()))
            count.append(int(mask.sum()))
        else:
            pred_mean.append(float((edges[i] + edges[i + 1]) / 2))
            actual.append(float("nan"))
            count.append(0)
    return CalibrationReport(
        n=len(p),
        brier=brier_score(p, y),
        log_loss=log_loss_score(p, y),
        accuracy=accuracy(p, y),
        base_rate=float(y.mean()) if len(y) else 0.0,
        bucket_edges=edges.tolist(),
        bucket_pred_mean=pred_mean,
        bucket_actual=actual,
        bucket_count=count,
    )


# ----- ROI simulation -----------------------------------------------------

@dataclass
class RoiReport:
    bets: int
    wins: int
    units_staked: float
    units_won: float
    roi_pct: float
    avg_edge_pct: float


def simulated_roi(p: Sequence[float], y: Sequence[int],
                   *, market_p: Sequence[float] | None = None,
                   american_odds: float = -110.0,
                   min_edge: float = 0.04, vig_buffer: float = 0.02,
                   stake_units: float = 1.0,
                   side: str = "nrfi") -> RoiReport:
    """Flat-stake ROI simulation when market_p is provided.

    `side`="nrfi" bets NRFI when our p − (market_p − buffer) ≥ min_edge.
    `side`="yrfi" bets YRFI under the symmetric condition. `side`="auto"
    picks whichever side has the larger edge per game.
    """
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    if market_p is None:
        # No market provided — assume -110 implied (.524 vigged) and fall
        # back to a model-only top/bottom slate.
        market_p = np.full_like(p, 0.524)
    market_p = np.asarray(market_p, dtype=float) - vig_buffer

    bets = 0; wins = 0; staked = 0.0; won = 0.0; edge_sum = 0.0
    payout = american_to_decimal(american_odds) - 1.0  # net per unit

    for i in range(len(p)):
        edge_nrfi = p[i] - market_p[i]
        edge_yrfi = (1 - p[i]) - (1 - market_p[i])
        if side == "auto":
            if edge_nrfi >= edge_yrfi and edge_nrfi >= min_edge:
                bet_side, edge = "nrfi", edge_nrfi
            elif edge_yrfi > edge_nrfi and edge_yrfi >= min_edge:
                bet_side, edge = "yrfi", edge_yrfi
            else:
                continue
        elif side == "nrfi" and edge_nrfi >= min_edge:
            bet_side, edge = "nrfi", edge_nrfi
        elif side == "yrfi" and edge_yrfi >= min_edge:
            bet_side, edge = "yrfi", edge_yrfi
        else:
            continue

        bets += 1
        edge_sum += edge
        staked += stake_units
        target = 1 if bet_side == "nrfi" else 0
        if y[i] == target:
            wins += 1
            won += stake_units * payout
        else:
            won -= stake_units

    roi = (won / staked * 100.0) if staked > 0 else 0.0
    return RoiReport(
        bets=bets, wins=wins,
        units_staked=staked, units_won=won, roi_pct=roi,
        avg_edge_pct=(edge_sum / bets * 100.0) if bets else 0.0,
    )
