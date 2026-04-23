"""
Deterministic dry-run slate for the That K Report module.

Eight MLB starting pitchers with realistic 2024-2025-season numbers,
chosen to exercise the full range of adjustments the model applies:

  * A K-heavy arsenal pitcher vs a whiff-prone lineup (Skubal v CHW)
  * Cross-handed matchup with a strict HP ump (Cole v BOS)
  * Neutral baseline in a dome (Ragans v MIN)
  * Veteran with a slider arsenal vs L-heavy lineup (Fried v LAA)
  * Young SP vs contact-heavy lineup (Skenes v HOU)
  * Cold-weather outdoor game (Nola v MIA)
  * Thin recent history -> sample warning (Snell v COL)
  * Flat line / low-confidence row (Morton v WSH)

No names / numbers claim to be live -- this is a deterministic
dry-run fixture so the CLI has something to render without any
external data source.  Hooking a real feed later replaces this
module, nothing else.
"""
from __future__ import annotations

from typing import List


def sample_slate() -> List[dict]:
    return [
        {
            "game_id": "2026-04-23-NYY-BOS",
            "line": 7.5,
            "pitcher": {
                "name": "Gerrit Cole", "team": "NYY", "throws": "R",
                "k_per_bf": 0.285, "expected_bf": 25,
                "swstr_pct": 0.135, "csw_pct": 0.315,
                "arsenal": {"FF": 0.105, "SL": 0.175, "CU": 0.140, "CH": 0.150},
                "recent_k_per_bf": [(0.31, 3), (0.28, 9), (0.33, 15)],
            },
            "lineup": {
                "team": "BOS", "swstr_pct": 0.118, "csw_pct": 0.302,
                "lhh_share": 0.55,
                "pitch_whiff": {"FF": 0.095, "SL": 0.165, "CU": 0.125, "CH": 0.120},
                "swstr_vs_R": 0.124,
            },
            "context": {
                "dome": False, "temp_f": 62.0, "wind_mph": 11.0, "wind_dir": "out",
                "umpire_name": "D. Bellino", "umpire_k_factor": 1.06,
                "park_k_factor": 0.99,
            },
        },
        {
            "game_id": "2026-04-23-DET-CHW",
            "line": 8.5,
            "pitcher": {
                "name": "Tarik Skubal", "team": "DET", "throws": "L",
                "k_per_bf": 0.305, "expected_bf": 26,
                "swstr_pct": 0.142, "csw_pct": 0.325,
                "arsenal": {"FF": 0.110, "SL": 0.185, "CH": 0.165},
                "recent_k_per_bf": [(0.33, 2), (0.30, 8), (0.35, 14), (0.29, 21)],
            },
            "lineup": {
                "team": "CHW", "swstr_pct": 0.128, "csw_pct": 0.318,
                "lhh_share": 0.35,
                "pitch_whiff": {"FF": 0.118, "SL": 0.195, "CH": 0.175},
                "swstr_vs_L": 0.135,
            },
            "context": {
                "dome": False, "temp_f": 68.0, "wind_mph": 7.0, "wind_dir": "cross",
                "umpire_name": "T. Timmons", "umpire_k_factor": 1.02,
                "park_k_factor": 1.01,
            },
        },
        {
            "game_id": "2026-04-23-KC-MIN",
            "line": 6.5,
            "pitcher": {
                "name": "Cole Ragans", "team": "KC", "throws": "L",
                "k_per_bf": 0.270, "expected_bf": 24,
                "swstr_pct": 0.125, "csw_pct": 0.305,
                "arsenal": {"FF": 0.100, "SL": 0.160, "CH": 0.155, "CU": 0.135},
                "recent_k_per_bf": [(0.27, 4), (0.29, 10), (0.25, 17)],
            },
            "lineup": {
                "team": "MIN", "swstr_pct": 0.110, "csw_pct": 0.290,
                "lhh_share": 0.40,
                "pitch_whiff": {},
                "swstr_vs_L": 0.112,
            },
            "context": {
                "dome": True, "temp_f": 72.0, "wind_mph": 0.0,
                "umpire_name": "C. Barber", "umpire_k_factor": 1.00,
                "park_k_factor": 1.00,
            },
        },
        {
            "game_id": "2026-04-23-ATL-LAA",
            "line": 6.5,
            "pitcher": {
                "name": "Max Fried", "team": "ATL", "throws": "L",
                "k_per_bf": 0.245, "expected_bf": 25,
                "swstr_pct": 0.115, "csw_pct": 0.298,
                "arsenal": {"FF": 0.095, "SI": 0.075, "SL": 0.155, "CU": 0.145, "CH": 0.140},
                "recent_k_per_bf": [(0.26, 3), (0.24, 9)],
            },
            "lineup": {
                "team": "LAA", "swstr_pct": 0.121, "csw_pct": 0.299,
                "lhh_share": 0.48,
                "pitch_whiff": {"SL": 0.175, "CU": 0.165},
                "swstr_vs_L": 0.128,
            },
            "context": {
                "dome": False, "temp_f": 74.0, "wind_mph": 6.0, "wind_dir": "in",
                "umpire_name": "L. Wolf", "umpire_k_factor": 1.03,
                "park_k_factor": 0.98,
            },
        },
        {
            "game_id": "2026-04-23-PIT-HOU",
            "line": 7.5,
            "pitcher": {
                "name": "Paul Skenes", "team": "PIT", "throws": "R",
                "k_per_bf": 0.295, "expected_bf": 23,
                "swstr_pct": 0.145, "csw_pct": 0.330,
                "arsenal": {"FF": 0.115, "SL": 0.190, "CU": 0.150, "SPL": 0.180},
                "recent_k_per_bf": [(0.30, 2), (0.32, 8), (0.28, 15), (0.31, 22)],
            },
            "lineup": {
                "team": "HOU", "swstr_pct": 0.098, "csw_pct": 0.278,
                "lhh_share": 0.38,
                "pitch_whiff": {"FF": 0.088, "SL": 0.155, "SPL": 0.165},
                "swstr_vs_R": 0.102,
            },
            "context": {
                "dome": False, "temp_f": 82.0, "wind_mph": 4.0, "wind_dir": "cross",
                "umpire_name": "A. Gonzalez", "umpire_k_factor": 0.97,
                "park_k_factor": 0.97,
            },
        },
        {
            "game_id": "2026-04-23-PHI-MIA",
            "line": 7.0,
            "pitcher": {
                "name": "Aaron Nola", "team": "PHI", "throws": "R",
                "k_per_bf": 0.260, "expected_bf": 25,
                "swstr_pct": 0.120, "csw_pct": 0.310,
                "arsenal": {"FF": 0.090, "SI": 0.080, "CU": 0.150, "CH": 0.140},
                "recent_k_per_bf": [(0.24, 3), (0.25, 9), (0.27, 16)],
            },
            "lineup": {
                "team": "MIA", "swstr_pct": 0.113, "csw_pct": 0.293,
                "lhh_share": 0.44,
                "pitch_whiff": {"CU": 0.155},
                "swstr_vs_R": 0.115,
            },
            "context": {
                "dome": False, "temp_f": 54.0, "wind_mph": 14.0, "wind_dir": "in",
                "umpire_name": "M. Winters", "umpire_k_factor": 1.02,
                "park_k_factor": 1.00,
            },
        },
        {
            "game_id": "2026-04-23-SF-COL",
            "line": 6.5,
            "pitcher": {
                "name": "Blake Snell", "team": "SF", "throws": "L",
                "k_per_bf": 0.290, "expected_bf": 22,
                "swstr_pct": 0.130, "csw_pct": 0.308,
                "arsenal": {"FF": 0.105, "SL": 0.170, "CU": 0.135, "CH": 0.150},
                "recent_k_per_bf": [(0.28, 4)],
            },
            "lineup": {
                "team": "COL", "swstr_pct": 0.115, "csw_pct": 0.295,
                "lhh_share": 0.33,
                "pitch_whiff": {"SL": 0.160},
                "swstr_vs_L": 0.120,
            },
            "context": {
                "dome": False, "temp_f": 60.0, "wind_mph": 12.0, "wind_dir": "out",
                "umpire_name": "R. Culbreth", "umpire_k_factor": 1.04,
                "park_k_factor": 1.03,
            },
        },
        {
            "game_id": "2026-04-23-BAL-WSH",
            "line": 5.5,
            "pitcher": {
                "name": "Charlie Morton", "team": "BAL", "throws": "R",
                "k_per_bf": 0.240, "expected_bf": 22,
                "swstr_pct": 0.108, "csw_pct": 0.285,
                "arsenal": {"FF": 0.085, "CU": 0.155, "FC": 0.110},
                "recent_k_per_bf": [(0.22, 3), (0.24, 10)],
            },
            "lineup": {
                "team": "WSH", "swstr_pct": 0.112, "csw_pct": 0.291,
                "lhh_share": 0.42,
                "pitch_whiff": {},
                "swstr_vs_R": 0.110,
            },
            "context": {
                "dome": False, "temp_f": 70.0, "wind_mph": 6.0, "wind_dir": "cross",
                "umpire_name": "J. West", "umpire_k_factor": 1.00,
                "park_k_factor": 1.00,
            },
        },
    ]
