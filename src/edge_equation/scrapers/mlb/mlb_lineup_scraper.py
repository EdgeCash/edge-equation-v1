"""
MLB Lineup Scraper — minimal stub.

Verbatim port available at:
    curl -fsSL https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/scrapers/mlb/mlb_lineup_scraper.py \\
        -o src/edge_equation/scrapers/mlb/mlb_lineup_scraper.py

ProjectionModel handles lineup=None with a neutral 1.0 factor — picks
just won't reflect star scratches until the port lands.
"""
from __future__ import annotations


class MLBLineupScraper:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_for_slate(self, slate):
        return slate

    def fetch(self, *args, **kwargs):
        return {}
