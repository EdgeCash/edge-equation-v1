"""
Deterministic dry-run results fixture for the Results card.

Pairs 1:1 with sample_slate.sample_slate() so the full dry-run loop
--projections on Day T -> results on Day T+1-- round-trips cleanly.
"""
from __future__ import annotations

from typing import Any, Dict, List


def sample_results() -> List[Dict[str, Any]]:
    """One verdict per starter in sample_slate, mixing Hits / Misses /
    Push so the renderer exercises every code path."""
    return [
        {"pitcher": "Gerrit Cole",    "line": 7.5, "actual": 9},   # Hit
        {"pitcher": "Tarik Skubal",   "line": 8.5, "actual": 6},   # Miss
        {"pitcher": "Cole Ragans",    "line": 6.5, "actual": 7},   # Hit
        {"pitcher": "Max Fried",      "line": 6.5, "actual": 5},   # Miss
        {"pitcher": "Paul Skenes",    "line": 7.5, "actual": 8},   # Hit
        {"pitcher": "Aaron Nola",     "line": 7.0, "actual": 7},   # Push
        {"pitcher": "Blake Snell",    "line": 6.5, "actual": 9},   # Hit
        {"pitcher": "Charlie Morton", "line": 5.5, "actual": 3},   # Miss
    ]


def sample_last_night_standout() -> Dict[str, Any]:
    """Previous-night K-of-the-Night payload matching
    sample_results()'s best outing."""
    return {
        "pitcher": "Blake Snell",
        "team": "SF",
        "opp": "COL",
        "ks": 9,
        "ip": "6.0",
        "swstr": 0.148,
        "line": 6.5,
    }


def sample_slate_hooks() -> Dict[str, Any]:
    """Slate-side hooks for the Stat Drop generator -- aligns with
    the sample slate so dry-runs feel internally consistent."""
    return {
        "umpire_top": {"name": "D. Bellino", "factor": 1.06},
        "lineup_swstr_leader": {"team": "CHW", "swstr": 0.128},
        "arsenal_edge": {"pitcher": "Tarik Skubal", "pitch": "SL", "swstr": 0.185},
        "form_streak": {"pitcher": "Paul Skenes", "starts": 4, "k_per_bf": 0.305},
    }
