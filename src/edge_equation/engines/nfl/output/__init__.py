"""NFL canonical output payload + email/api adapters — skeleton.

The full payload mirrors `engines/full_game/output/payload.py` so the
email TOP BOARD format reads identically across NRFI / Props / FG /
NFL / NCAAF. Implementation lands in F-2.
"""

from .payload import NFLOutput

__all__ = ["NFLOutput"]
