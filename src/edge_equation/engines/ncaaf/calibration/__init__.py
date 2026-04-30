"""NCAAF calibration layer — skeleton.

College spread distributions are wider than NFL (more 30+ point
spreads) AND have similar key-number clusters at -3 / +3 / -7 / +7.
Plus a tail effect at -10 / -14 / -17 from common SEC-vs-MAC
matchup blowout patterns.

The calibration layer needs to handle:
* Wider distribution → fatter prior weight on extreme spreads.
* Same key-number lookup adjustment as NFL.
* Separate calibration regime for FBS-vs-FCS body-bag games (often
  too lopsided for any market efficiency to exist).

Phase F-1 ships none of this — just the package shape.
"""
