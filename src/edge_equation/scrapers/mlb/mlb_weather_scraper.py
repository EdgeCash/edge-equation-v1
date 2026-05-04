"""
MLB Weather Scraper — minimal stub.

Verbatim port available at:
    curl -fsSL https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/scrapers/mlb/mlb_weather_scraper.py \\
        -o src/edge_equation/scrapers/mlb/mlb_weather_scraper.py

ProjectionModel handles weather=None with a neutral 1.0 factor, so
omitting weather data is safe — totals just won't have wind/temp
adjustments until the port lands.
"""
from __future__ import annotations


class MLBWeatherScraper:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_for_slate(self, slate):
        return slate

    def fetch(self, *args, **kwargs):
        return {}
