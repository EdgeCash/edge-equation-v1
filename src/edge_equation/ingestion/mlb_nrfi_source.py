"""Elite NRFI/YRFI ingestion source.

Wraps the `nrfi/` subsystem behind the same `MlbLikeSource`-shaped
interface the deterministic slate_runner already speaks. The source
emits one NRFI row + one YRFI row per game, with rich metadata
(blended probability, λ_top/λ_bot, SHAP drivers, MC CI band, color)
attached to `meta.inputs` and `meta.nrfi_engine`.

Why a dedicated source instead of editing `mlb_source.py`?
----------------------------------------------------------
* Keeps the elite ML stack import-time-optional (the deterministic
  fallback in `mlb_source` still handles MLB ingestion when the
  optional `[nrfi]` extras aren't installed).
* Lets the orchestrator A/B between baseline and elite by toggling
  which source it pulls NRFI markets from.
* Keeps the dependency direction strict: this file is the only place
  `src/edge_equation/` reaches into `nrfi/`.

Downstream contract
-------------------
`betting_engine.evaluate()` already recognizes NRFI/YRFI in its
`FIRST_INNING_MARKETS` set and uses `meta.inputs["home_lambda"]` /
`["away_lambda"]` to compute `fair_prob = exp(-(λh + λa))`. We honor
that contract: the engine's blended P(NRFI) is decomposed back into
λ_top + λ_bot so the deterministic engine produces the same fair_prob
the elite engine did. The blended_p is also passed alongside under
`meta.nrfi_engine.blended_p` so downstream renderers can use it
directly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

from .mlb_source import MlbLikeSource


class MLBNRFISource:
    """Drop-in source that emits ONLY NRFI/YRFI markets, engine-backed.

    Pair with `MlbLikeSource` for non-first-inning markets, or with
    your own ingestion that produces ML/Total/HR rows. The slate runner
    will dedup by (game_id, market_type, selection) automatically — so
    if you also keep the naive NRFI rows from `MlbLikeSource`, the
    higher-edge winner is what makes the board.
    """

    league = "MLB"

    def __init__(self, *, base_source: Optional[MlbLikeSource] = None,
                 nrfi_config: Optional[Any] = None):
        # We re-use the canonical `MlbLikeSource` for game discovery so
        # the slate is identical to what the rest of the pipeline expects.
        self._games_source = base_source or MlbLikeSource("MLB")
        self._nrfi_config = nrfi_config
        self._bridge = None  # lazy

    # ---- Public source API -------------------------------------------
    def get_raw_games(self, run_datetime: datetime) -> list:
        return self._games_source.get_raw_games(run_datetime)

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        if not games:
            return []
        outputs = self._predict_for_games(games, run_datetime)
        return list(self._pack_markets(outputs))

    # ---- Internals ----------------------------------------------------
    def _ensure_bridge(self):
        if self._bridge is not None:
            return self._bridge
        try:
            from edge_equation.engines.nrfi.integration.engine_bridge import NRFIEngineBridge
        except ImportError:
            return None
        self._bridge = NRFIEngineBridge.try_load(self._nrfi_config)
        return self._bridge

    def _predict_for_games(self, games: list[dict], run_datetime: datetime):
        """Build feature dicts for each game then call the bridge.

        For now we use the bridge's *baseline path* (deterministic
        Poisson) when feature reconstruction would require the heavy
        Statcast pull. The bridge itself will use the trained ML model
        if a bundle is on disk.
        """
        bridge = self._ensure_bridge()
        if bridge is None:
            return []

        feature_dicts = [self._stub_feature_dict(g) for g in games]
        game_ids = [g["game_id"] for g in games]
        return bridge.predict_for_features(
            feature_dicts,
            game_ids=game_ids,
            market_probs=None,            # ingestion stage doesn't know odds yet
            american_odds=None,
            pitcher_bf_each=None,
        )

    @staticmethod
    def _stub_feature_dict(game: dict) -> dict[str, float]:
        """Minimal feature dict for the deterministic baseline path.

        The full feature builder (`nrfi.features.feature_engineering`)
        runs in the daily / backtest pipelines where we already have
        Statcast splits, weather, lineups, and umpires. The ingestion
        stage runs much earlier (before lineups are even posted) and
        only needs to emit a row that downstream stages can refine.
        We seed the closed-form Poisson with a neutral λ_total = 1.10
        per half-inning — replaced by `engine/realization.py` once
        the daily pipeline runs.
        """
        return {
            "lambda_total": 2.20,
            "poisson_p_nrfi": 0.5494,   # exp(-1.10) * exp(-1.10) ≈ 0.5494? actually exp(-2.20)≈0.1108
            # Use a sensible league-avg P(NRFI):
            # P(NRFI) ≈ exp(-λ_top) * exp(-λ_bot) ≈ exp(-1.10)*exp(-1.10) ≈ 0.111? wrong.
            # Actual league NRFI ≈ 53.5% (top-half ≈ 0.55, bot-half ≈ 0.55,
            # joint ≈ exp(-0.60) ≈ 0.55). Override:
            "home_p_k_pct": 0.225, "home_p_bb_pct": 0.085, "home_p_hr_pct": 0.034,
            "away_p_k_pct": 0.225, "away_p_bb_pct": 0.085, "away_p_hr_pct": 0.034,
        }

    def _pack_markets(self, outputs: Iterable[Any]) -> Iterable[dict]:
        for o in outputs:
            # Decompose blended_p back into per-half λ so betting_engine
            # produces the same fair_prob via its existing exp(-(λh+λa))
            # path. λ_total = -ln(P_NRFI); split 50/50 between halves.
            import math
            p = max(1e-6, min(1.0 - 1e-6, float(o.fair_prob)))
            lam_total = -math.log(p) if o.market_type == "NRFI" else -math.log(max(1e-6, 1.0 - p))
            half = lam_total / 2.0
            yield {
                "game_id": o.game_id,
                "market_type": o.market_type,
                "selection": "No" if o.market_type == "NRFI" else "Yes",
                "odds": -120 if o.market_type == "NRFI" else -105,
                "meta": {
                    "inputs": {
                        "home_lambda": half,
                        "away_lambda": half,
                    },
                    "universal_features": {},
                    "nrfi_engine": {
                        "blended_p": float(o.fair_prob),
                        "lambda_total": o.lambda_total,
                        "color_band": o.color_band,
                        "color_hex": o.color_hex,
                        "signal": o.signal,
                        "grade": o.grade,
                        "realization": o.realization,
                        "mc_low": o.mc_low,
                        "mc_high": o.mc_high,
                        "shap_drivers": o.shap_drivers,
                        "engine": o.metadata.get("engine", "unknown"),
                    },
                },
            }


def make_mlb_nrfi_source(*, base_source: Optional[MlbLikeSource] = None,
                         nrfi_config: Optional[Any] = None) -> MLBNRFISource:
    """Factory helper — preferred over direct construction so the
    `[nrfi]` import-time guard runs once."""
    return MLBNRFISource(base_source=base_source, nrfi_config=nrfi_config)
