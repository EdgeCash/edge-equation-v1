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
    # Also surface as a GitHub Actions ``::notice::`` so the choice
    # appears in the Daily Master workflow UI alongside the engine
    # logs. Without this the operator can't tell from the log alone
    # whether ILP / deduped actually fired or whether a missing env
    # var silently fell back to baseline.
    print(
        f"::notice::parlay strategy: engine={engine_kind} "
        f"strategy={raw_name!r} (source={source})",
        flush=True,
    )
    return get_strategy(raw_name)


def log_strategy_summary(env: Optional[dict[str, str]] = None) -> None:
    """Pre-flight banner the orchestrator prints once at startup.

    Lists every engine_kind with the strategy it will resolve to + the
    source (per-engine env / global env / default). Same information as
    the per-call ``::notice::`` above but emitted once before the
    parlay engines run, so the operator can see config drift in the
    very first lines of the Daily Master log.
    """
    env_dict = env if env is not None else os.environ
    print("::group::Parlay strategy summary", flush=True)
    for engine_kind in ENV_PER_ENGINE:
        per_engine_var = ENV_PER_ENGINE[engine_kind]
        if per_engine_var and env_dict.get(per_engine_var, "").strip():
            name = env_dict[per_engine_var].strip()
            source = per_engine_var
        elif env_dict.get(ENV_GLOBAL, "").strip():
            name = env_dict[ENV_GLOBAL].strip()
            source = ENV_GLOBAL
        else:
            name = _DEFAULT_PER_ENGINE.get(engine_kind, "baseline")
            source = f"default[{engine_kind}]"
        # Warn loudly when we silently fall back to the default --- the
        # operator usually wants to know they didn't set the env var.
        if source.startswith("default["):
            print(
                f"::warning::parlay strategy: engine={engine_kind} "
                f"strategy={name!r} -- using built-in default. Set "
                f"{per_engine_var} in the workflow to override.",
                flush=True,
            )
        else:
            print(
                f"  engine={engine_kind} strategy={name!r} (source={source})",
                flush=True,
            )
    print("::endgroup::", flush=True)
