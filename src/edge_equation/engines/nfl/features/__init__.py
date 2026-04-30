"""NFL feature engineering.

Skeleton — placeholders for the per-team / per-player feature
builders. See ``../README.md`` for the planned feature inventory.

Planned modules (NOT yet implemented):

* ``team_rates.py`` — rolling per-team expected points, points
  allowed, EPA per play, success rate, pace.
* ``qb_rates.py`` — per-QB rolling pass yards, completion %, TDs,
  pressure / sack rate when available.
* ``rb_rates.py`` — per-RB carries, yards per carry, target share.
* ``wr_rates.py`` — per-WR target share, air yards, separation
  proxies.
* ``def_rates.py`` — per-team defensive yards allowed, EPA-per-play
  allowed, pressure rate generated.
* ``pace_environment.py`` — game pace + script projection (early
  blowout filtering, garbage-time exclusion).
* ``injury_status.py`` — wires Wednesday/Thursday/Friday practice
  reports into the per-game projection.
"""
