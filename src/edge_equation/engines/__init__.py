"""Sport / market specific prediction engines.

Each engine owns its own feature builder, model bundle, calibration,
and output adapters. Shared primitives — math, data fetching, posting,
publishing — live one level up under ``src/edge_equation/`` and are
imported by every engine.

Engines live as subpackages here::

    src/edge_equation/engines/
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
       ├── output/               # Canonical output payload + adapters
       └── integration/          # Bridge to the shared core (single import surface)

2. Register the engine source via ``src/edge_equation/ingestion/source_factory.py``.
3. Wire posting/email rendering through ``src/edge_equation/posting/``.
4. Plug into the slate runner (``src/edge_equation/engine/slate_runner.py``).

The NRFI engine is the canonical reference implementation — copy its
patterns, not its scaffolding. Engine-local utilities (caching, logging,
rate limiting) should be hoisted up into ``src/edge_equation/utils/``
so the next two engines don't duplicate them.
"""
