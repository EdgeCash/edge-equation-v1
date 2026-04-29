"""MLB player-props engine — placeholder for Phase 4.

Status: skeleton only. No code yet.

When this engine is built it must:

* Source market lines from The Odds API (DraftKings/FanDuel/BetMGM
  player props) — the legacy PrizePicks scraper is being deprecated
  in this same migration cycle.
* Run per-prop projection models (HR, hits, K, RBI, total bases,
  etc.) backed by Statcast features.
* Emit picks via the canonical NRFIOutput-style payload so the daily
  email / API / dashboard consume one shape across all engines.
* Tier classification is **edge-based** (model_p − vig-adjusted
  market_p), not raw probability. NRFI's raw-probability ladder is
  market-symmetric and only applies to ~50/50 markets like NRFI/YRFI.

This package now owns the standard engine subpackages:
``features/``, ``models/``, ``calibration/``, ``output/``, and ``source/``.
"""
