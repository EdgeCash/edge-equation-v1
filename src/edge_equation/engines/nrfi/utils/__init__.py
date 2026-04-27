"""NRFI engine-local utilities.

Most utilities have been hoisted to ``src/edge_equation/utils/`` so
the next two engines (props_prizepicks, full_game) can share them
without duplication. What stays here is engine-specific:

* ``colors`` — the NRFI/YRFI 5-band gradient (deep red → deep green)
  keyed to the symmetric ~50/50 first-inning probability ladder.
  Other engines use edge-based tier ladders, so this isn't shared.

For shared helpers, import directly from ``edge_equation.utils``::

    from edge_equation.utils.caching import disk_cache
    from edge_equation.utils.kelly import kelly_stake
    from edge_equation.utils.logging import get_logger
    from edge_equation.utils.rate_limit import global_limiter
"""
