"""
MLB Odds Scraper — re-export of the v1 adapter.

Scrapers' code refers to `scrapers.mlb.mlb_odds_scraper.MLBOddsScraper`;
this module forwards that import to v1's adapter so the verbatim
orchestrator port works without further rewrites.
"""
from edge_equation.exporters.mlb._odds_adapter import MLBOddsScraper

__all__ = ["MLBOddsScraper"]
