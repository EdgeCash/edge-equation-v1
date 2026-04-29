"""Sport / market specific prediction engines.

Each engine owns its own feature builder, model bundle, calibration,
output adapters, and source boundary. Shared primitives — data, caching,
utilities, math, posting, publishing, and backtesting — live under
``edge_equation.engines.core`` with backward-compatible top-level imports
kept for shipped scripts.

Engines live as subpackages here::

    src/edge_equation/engines/
    ├── core/              # Shared data/cache/math/posting/publishing facades
    ├── nrfi/              # MLB first-inning NRFI/YRFI (flagship; production)
    ├── props_prizepicks/  # MLB player props (data via The Odds API; in dev)
    └── full_game/         # MLB ML / Total / F5 / Run Line (in dev)

Adding a new engine
-------------------
1. Create ``src/edge_equation/engines/<name>/`` with at minimum::

       <name>/
       ├── __init__.py
       ├── config.py             # Engine-specific tuning constants
       ├── features/             # Engine-specific feature builders
       ├── models/               # Engine-specific model wrappers
       ├── calibration/          # Reliability / calibration adapters
       ├── output/               # Canonical output payload + adapters
       └── source/               # Engine-owned market/data source boundary

2. Register the engine source via ``src/edge_equation/ingestion/source_factory.py``.
3. Wire posting/email rendering through ``src/edge_equation/posting/``.
4. Plug into the slate runner (``src/edge_equation/engine/slate_runner.py``).

The NRFI engine is the canonical reference implementation — copy its
patterns, not its scaffolding. Engine-local utilities that become shared
should be hoisted into ``edge_equation.engines.core`` so props and full-game
engines do not duplicate them.
"""
