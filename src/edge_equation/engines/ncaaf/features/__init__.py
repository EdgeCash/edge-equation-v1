"""NCAAF feature engineering — skeleton.

Planned modules (NOT yet implemented):

* ``team_rates.py`` — rolling per-team rates, scaled by conference
  tier (SEC vs G5 vs FCS produce structurally different priors).
* ``recruit_ratings.py`` — 247 / Rivals composite ratings as
  preseason talent-prior input.
* ``transfer_portal.py`` — adjustments for high-impact transfers
  (especially QBs) entering / leaving a roster.
* ``conf_tier.py`` — conference / division mapping for the prior
  blend.
* ``pace_environment.py`` — pace classification (Air Raid vs
  triple-option vs balanced) which moves expected total points
  significantly.
* ``injury_status.py`` — practice-report scraping; college reports
  are less standardized than NFL so this needs more care.
"""
