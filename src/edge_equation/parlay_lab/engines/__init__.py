"""Engine registry --- map of CLI name -> ParlayEngine subclass.

Adding a new engine = drop the file in this directory + register
its class here. The shootout CLI uses :data:`ENGINES` to resolve
``--engines a,b,c`` flags.
"""

from __future__ import annotations

from typing import Type

from ..base import ParlayEngine
from .baseline import BaselineEngine
from .deduped import SameGameDedupedEngine


ENGINES: dict[str, Type[ParlayEngine]] = {
    BaselineEngine.name: BaselineEngine,
    SameGameDedupedEngine.name: SameGameDedupedEngine,
}


def resolve(name: str) -> ParlayEngine:
    """Instantiate an engine by its CLI name. Raises KeyError on miss."""
    cls = ENGINES[name]
    return cls()


def all_engines() -> list[ParlayEngine]:
    """Instantiate every registered engine in registration order."""
    return [cls() for cls in ENGINES.values()]
