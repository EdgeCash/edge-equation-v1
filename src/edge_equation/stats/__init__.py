"""
Stats layer.

Converts completed game results into the engine's feature inputs
(strength_home, strength_away, off_env, def_env, pace, dixon_coles_adj)
so CSV-only slates (KBO, NPB) and live odds without meta.inputs can be
scored by the engine instead of being discarded at evaluation time.

Modules:
- results.py      game-result dataclass + SQLite store (migration v2)
- elo.py          per-sport Elo rating updater + win-probability predictor
- team_stats.py   rolling offensive / defensive / pace summaries per team
- composer.py     top-level FeatureComposer that stitches the three together
- csv_loader.py   weekly CSV loader for leagues without a results feed

All deterministic: every output is a pure function of the recorded game
history plus a handful of league constants. No ML, no black boxes.
"""
