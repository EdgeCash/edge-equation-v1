"""
Ingestion layer: fetch raw data from external sources.
"""
from .sources import IngestionSource, IngestionResult
from .normalizer import NormalizedFrame

__all__ = ["IngestionSource", "IngestionResult", "NormalizedFrame"]
