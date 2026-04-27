"""Logging helper — centralised so every NRFI module formats consistently
with the rest of the Edge Equation codebase."""

from __future__ import annotations

import logging
from typing import Optional

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_CONFIGURED = False


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return a module logger; configure root once on first call."""
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(level=level or "INFO", format=_FORMAT)
        _CONFIGURED = True
    logger = logging.getLogger(name)
    if level:
        logger.setLevel(level)
    return logger
