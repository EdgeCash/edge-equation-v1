"""
Park factor lookup. Stub — replaced verbatim during cutover by curl-ing
edge-equation-scrapers/exporters/mlb/park_factors.py:

    curl -fsSL https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/exporters/mlb/park_factors.py \\
        -o src/edge_equation/exporters/mlb/park_factors.py

Until that fetch happens this stub returns 1.0 for every team, which is
neutral — projections still run, they just don't get park adjustment.
The model_meta in projections.py records park_factor=1.0 in that case.
"""
from __future__ import annotations


def park_factor(home_team: str) -> float:
    """Park factor for the home team's venue. 1.0 = neutral.

    NOTE: stub. Replace with the scrapers verbatim file before cutover.
    """
    return 1.0
