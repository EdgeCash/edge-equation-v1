"""Env-var-driven strategy resolver for the MLB parlay engines.

Resolution order (first non-empty wins):

  1. Per-engine override:
       MLB_GAME_PARLAY_STRATEGY    (game-results parlay)
       MLB_PROPS_PARLAY_STRATEGY   (player-props parlay)

  2. Universal override:
       MLB_PARLAY_STRATEGY

  3. Built-in default per engine_kind:
       game_results -> "baseline"
       player_props -> "baseline"

  4. ``Strategy`` callable (see ``strategies.py``).

Defaults stay on baseline so this PR ships without any behavior
change. Promoting a winning strategy to production is then a
one-line workflow change (``MLB_GAME_PARLAY_STRATEGY: ilp``)
rather than a code change.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .strategies import Strategy, get_strategy


log = logging.getLogger(__name__)


# Stable env-var names. Don't rename without also updating the
# Daily Master workflow.
ENV_GLOBAL = "MLB_PARLAY_STRATEGY"
ENV_PER_ENGINE: dict[str, str] = {
    "game_results": "MLB_GAME_PARLAY_STRATEGY",
    "player_props": "MLB_PROPS_PARLAY_STRATEGY",
}


# Built-in defaults --- changeable here without touching the workflow.
# As of the parlay_lab Phase 3 results, baseline is the conservative
# choice; flip in the workflow when ready to promote.
_DEFAULT_PER_ENGINE: dict[str, str] = {
    "game_results": "baseline",
    "player_props": "baseline",
}


def resolve_strategy(
    engine_kind: str = "default", *, env: Optional[dict[str, str]] = None,
) -> Strategy:
    """Resolve the strategy for one engine kind.

    ``engine_kind`` is one of ``game_results`` or ``player_props``.
    Anything else falls through to the global default.

    The optional ``env`` dict lets tests inject overrides without
    touching ``os.environ``; defaults to the live process env.
    """
    env_dict = env if env is not None else os.environ
    raw_name: Optional[str] = None
    source: str = ""

    per_engine_var = ENV_PER_ENGINE.get(engine_kind)
    if per_engine_var and (val := env_dict.get(per_engine_var, "").strip()):
        raw_name = val
        source = per_engine_var
    elif (val := env_dict.get(ENV_GLOBAL, "").strip()):
        raw_name = val
        source = ENV_GLOBAL
    else:
        raw_name = _DEFAULT_PER_ENGINE.get(engine_kind, "baseline")
        source = f"default[{engine_kind}]"

    log.info(
        "parlay strategy: engine=%s strategy=%r (source=%s)",
        engine_kind, raw_name, source,
    )
    return get_strategy(raw_name)
