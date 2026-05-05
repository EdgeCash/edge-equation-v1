"""
Global engine registry for Edge Equation.
Maps sport → engine runner class.
This file must import every engine that the system can run.
"""

# -------------------------
# MLB Engines
# -------------------------

from edge_equation.engines.nrfi.run_daily import NrfRunner
from edge_equation.engines.props_prizepicks.run_daily import MLBPropsRunner
from edge_equation.engines.full_game.run_daily import MLBFullGameRunner

# -------------------------
# WNBA Engine
# -------------------------

from edge_equation.engines.wnba.run_daily import WNBARunner


# -------------------------
# Engine Registry
# -------------------------

ENGINE_REGISTRY = {
    "mlb_nrfi": NrfRunner,
    "mlb_props": MLBPropsRunner,
    "mlb_fullgame": MLBFullGameRunner,
    "wnba": WNBARunner,
}


def get_engine(engine_key: str):
    """
    Returns the engine class for a given key.
    """
    return ENGINE_REGISTRY.get(engine_key)
