"""MLB full-game engine — placeholder for Phase 5.

Status: skeleton only. No code yet.

Markets covered when this engine is built:

* Moneyline (full game)
* Total runs (full game)
* Run line / spread
* First-five-innings ML
* First-five-innings total
* Team totals (per team, full game and F5)

Reuses the deterministic-core math primitives already shipped in
``src/edge_equation/math/``: Bradley-Terry for win probability,
Poisson + Dixon-Coles correlation for totals, Pythagorean expectation
for season-strength features, exponential decay for recency weighting,
isotonic regression for calibration, and KellyAdaptive for stake sizing.

Tier classification is **edge-based** for non-50/50 markets (favorites
at -150, etc.). The NRFI raw-probability ladder does not generalise
here — see ``edge_equation.engines.nrfi.utils.colors`` for the symmetric
ladder and the audit thread for why.

Track work under PR series claude/full-game-* after props ships.
"""
