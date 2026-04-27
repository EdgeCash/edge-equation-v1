"""Reliability diagrams + feature importance + ROI plots.

Matplotlib + seaborn. All functions return the Figure so callers can
either `.show()` or `.savefig(...)` themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .metrics import CalibrationReport


def _import_mpl():
    import matplotlib
    matplotlib.use("Agg")  # headless safe
    import matplotlib.pyplot as plt  # type: ignore
    return plt


def reliability_plot(report: CalibrationReport,
                     title: str = "NRFI Reliability",
                     savepath: Optional[str | Path] = None):
    plt = _import_mpl()
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="Perfect")
    ax.plot(report.bucket_pred_mean, report.bucket_actual,
             marker="o", lw=2, label="Engine")
    for x, y, n in zip(report.bucket_pred_mean, report.bucket_actual,
                        report.bucket_count):
        if not np.isnan(y):
            ax.annotate(str(n), (x, y), fontsize=8,
                         xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("Predicted NRFI probability")
    ax.set_ylabel("Empirical NRFI rate")
    ax.set_title(f"{title}\nN={report.n}  Brier={report.brier:.4f}  LogLoss={report.log_loss:.4f}")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    if savepath:
        fig.savefig(savepath, dpi=150)
    return fig


def probability_histogram(p: Sequence[float], y: Sequence[int],
                           savepath: Optional[str | Path] = None):
    plt = _import_mpl()
    p = np.asarray(p); y = np.asarray(y, dtype=int)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(p[y == 1], bins=20, alpha=0.6, label="NRFI=1", color="#1b5e20")
    ax.hist(p[y == 0], bins=20, alpha=0.6, label="YRFI (0)", color="#b00020")
    ax.set_xlabel("Predicted P(NRFI)")
    ax.set_ylabel("Games")
    ax.legend()
    fig.tight_layout()
    if savepath:
        fig.savefig(savepath, dpi=150)
    return fig


def feature_importance_plot(feature_names: Sequence[str],
                             importances: Sequence[float],
                             top_n: int = 30,
                             savepath: Optional[str | Path] = None):
    plt = _import_mpl()
    pairs = sorted(zip(feature_names, importances), key=lambda kv: kv[1],
                    reverse=True)[:top_n]
    names = [k for k, _ in pairs][::-1]
    vals = [v for _, v in pairs][::-1]
    fig, ax = plt.subplots(figsize=(8, max(4, 0.25 * top_n)))
    ax.barh(names, vals, color="#1f77b4")
    ax.set_xlabel("Importance")
    ax.set_title(f"Top {top_n} features")
    fig.tight_layout()
    if savepath:
        fig.savefig(savepath, dpi=150)
    return fig


def roi_curve(p: Sequence[float], y: Sequence[int],
               market_p: Sequence[float], thresholds: Sequence[float] = (0.02, 0.04, 0.06, 0.08, 0.10),
               savepath: Optional[str | Path] = None):
    """ROI as a function of the min-edge filter."""
    from .metrics import simulated_roi
    plt = _import_mpl()
    rois, bets = [], []
    for t in thresholds:
        r = simulated_roi(p, y, market_p=market_p, min_edge=t, side="auto")
        rois.append(r.roi_pct); bets.append(r.bets)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(list(thresholds), rois, marker="o", color="#1f77b4")
    for t, r, b in zip(thresholds, rois, bets):
        ax.annotate(f"n={b}", (t, r), xytext=(3, 3), textcoords="offset points",
                     fontsize=8)
    ax.axhline(0, color="k", lw=1, linestyle="--")
    ax.set_xlabel("Min edge")
    ax.set_ylabel("ROI %")
    ax.set_title("Simulated ROI vs. edge threshold")
    fig.tight_layout()
    if savepath:
        fig.savefig(savepath, dpi=150)
    return fig
