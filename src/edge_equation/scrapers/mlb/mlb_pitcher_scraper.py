"""
MLB Pitcher Scraper — minimal stub.

Production-quality replacement (probable starters + season ERA/FIP +
pitcher-quality factor for the projection model) lives in scrapers as
scrapers/mlb/mlb_pitcher_scraper.py and can be ported with:

    curl -fsSL https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/scrapers/mlb/mlb_pitcher_scraper.py \\
        -o src/edge_equation/scrapers/mlb/mlb_pitcher_scraper.py

The projection model (exporters.mlb.projections.ProjectionModel.project_matchup)
already accepts away_sp=None / home_sp=None and applies a neutral 1.0
factor when no data is supplied, so the orchestrator runs end-to-end
without this stub doing anything — it just means probabilities don't
reflect SP quality until the verbatim port lands.
"""
from __future__ import annotations


class MLBPitcherScraper:
    """No-op compatible interface so the orchestrator can call .fetch_for_slate()."""

    def __init__(self, *args, **kwargs):
        pass

    def fetch_for_slate(self, slate):
        """Returns the slate unchanged — pitcher fields stay None / absent."""
        return slate

    def fetch(self, *args, **kwargs):
        return {}
