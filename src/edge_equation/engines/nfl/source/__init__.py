"""NFL data-source layer — skeleton.

Planned modules:

* ``odds_fetcher.py`` — wraps The Odds API per-event endpoint with
  ``sport_key="americanfootball_nfl"`` and the per-event prop
  markets from `nfl/markets.py`.
* ``schedule.py`` — pulls the season schedule from a public NFL
  source (nflverse / sportradar) so the engine knows kickoff times,
  bye weeks, prime-time slots.
* ``injuries.py`` — Wednesday/Thursday/Friday/Sunday-AM injury
  report scraper. Deduplicates across sources; carries the injury
  status timestamps so the engine can prefer fresher reads.
* ``weather.py`` — outdoor-venue weather lookup (Open-Meteo, like
  the NRFI engine). Reuses the weather impact scoring from
  ``football_core.weather``.
* ``storage.py`` — DuckDB persistence (mirror of NRFI's
  ``data/storage.py``). Tables: ``nfl_games``, ``nfl_team_features``,
  ``nfl_player_features``, ``nfl_predictions``, ``nfl_actuals``.

Phase F-1 ships none of this — just the package shape.
"""
