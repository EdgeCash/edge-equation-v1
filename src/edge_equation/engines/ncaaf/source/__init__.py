"""NCAAF data-source layer — skeleton.

Planned modules:

* ``odds_fetcher.py`` — Odds API per-event with
  ``sport_key="americanfootball_ncaaf"``. Books drop ML on big
  blowouts; orchestrator handles missing markets.
* ``schedule.py`` — cfbfastR / sportsreference scrape for the
  season schedule + bye weeks + bowl assignments.
* ``recruit_ratings.py`` — 247 composite ratings ingestion (annual
  refresh).
* ``transfer_portal.py`` — On3 / 247 portal tracker integration.
* ``injuries.py`` — best-effort scrape of conference / school
  injury reports (college reports are less standardized than NFL).
* ``weather.py`` — outdoor venue lookup. Many smaller venues lack
  good weather coverage; expect more "unknown weather" rows.
* ``storage.py`` — DuckDB persistence (mirror of NFL's). Tables:
  ``ncaaf_games``, ``ncaaf_team_features``, ``ncaaf_predictions``,
  ``ncaaf_actuals``.

Phase F-1 ships none of this — just the package shape.
"""
