"""Kelly staking helpers for engine code.

The implementation lives in :mod:`edge_equation.utils.kelly` for legacy
callers.  This re-export gives new engine packages a shared-core import path.
"""

from edge_equation.utils.kelly import *  # noqa: F401,F403
