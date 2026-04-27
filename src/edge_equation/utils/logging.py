"""Centralised logger factory used across the deterministic core
and every engine subpackage.

Usage::

    from edge_equation.utils.logging import get_logger
    log = get_logger(__name__)
    log = get_logger(__name__, "DEBUG")   # optional per-logger override

Idempotent: calling `get_logger(name)` twice returns the same logger
without duplicating handlers. The `level` kwarg is honoured on every
call so the last writer wins for a given logger.
"""

from __future__ import annotations

import logging
from typing import Optional

# Project-standard format. `-7s` left-justifies the level field so
# WARNING / INFO / DEBUG line up cleanly across modules.
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return a configured logger for `name`.

    Parameters
    ----------
    name : Logger name (typically `__name__`).
    level : Optional log level override (e.g. "DEBUG", "INFO"). When
        omitted the logger keeps its existing level (INFO on first
        configuration).
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    if level:
        logger.setLevel(level)
    return logger
