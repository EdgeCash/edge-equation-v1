"""Data ingestion layer: free public sources only.

Modules
-------
scrapers_etl  : MLB Stats API + pybaseball + Baseball Savant ABS + lineups.
weather       : Open-Meteo forecast & historical archive.
park_factors  : Static dict of all 30 MLB parks (lat/long, altitude, factors).
storage       : DuckDB schema + helpers for persisting raw & feature tables.
"""
