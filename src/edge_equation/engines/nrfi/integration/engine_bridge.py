"""`NRFIEngineBridge` — the single entry point that the deterministic
Edge Equation core uses to talk to the elite NRFI engine.

Why a bridge?
-------------
The deterministic core (`src/edge_equation/`) is dependency-light by
policy — it cannot import xgboost / shap / pybaseball directly. The
bridge encapsulates all of that behind a clean façade. If the optional
`[nrfi]` extras aren't installed, `available()` returns False and the
ingestion source quietly skips NRFI/YRFI markets — the rest of the
engine keeps running.

Output is a list of plain dicts ready to be wrapped in `Pick` objects
by `mlb_nrfi_source.py`. We intentionally do NOT construct `Pick`
objects here — that would cause a hard dependency loop between
nrfi/ and src/edge_equation/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from ..config import NRFIConfig, get_default_config
from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class NRFIBridgeOutput:
    """One NRFI pick payload, ready for `Pick` construction."""

    game_id: str
    market_type: str           # "NRFI" or "YRFI"
    selection: str             # "NRFI" or "YRFI" — same as market_type for clarity
    fair_prob: Decimal         # Calibrated 0..1
    nrfi_pct: float            # 0..100
    lambda_total: float
    color_band: str
    color_hex: str
    signal: str
    grade: str
    realization: int
    edge: Optional[Decimal] = None
    kelly: Optional[Decimal] = None
    market_prob: Optional[float] = None
    mc_low: Optional[float] = None
    mc_high: Optional[float] = None
    shap_drivers: list[tuple[str, float]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class NRFIEngineBridge:
    """Façade over the optional NRFI ML stack.

    Typical usage from `mlb_nrfi_source.py`::

        bridge = NRFIEngineBridge.try_load()
        if not bridge.available():
            return []   # quietly skip — extras not installed
        outputs = bridge.predict_for_games(games, market_lookup)
        return [pack_into_market_dict(o) for o in outputs]
    """

    def __init__(self, config: NRFIConfig | None = None,
                 inference_engine=None):
        self.cfg = config or get_default_config()
        self._engine = inference_engine
        self._loaded = inference_engine is not None

    # -- Loading ----------------------------------------------------------
    @classmethod
    def try_load(cls, config: NRFIConfig | None = None) -> "NRFIEngineBridge":
        """Best-effort load. Returns a disabled bridge if extras missing.

        Resolution order:
        1. Local cache at `cfg.model_dir` (fast path; the trained bundle
           was previously fetched and unpacked here).
        2. Cloudflare R2 — fetch `nrfi/bundles/latest.bundle`, unpack
           into `cfg.model_dir`, then load. Phase 2c handoff for cron
           retrains and remote-environment daily runs.
        3. Disabled bridge — Poisson baseline path. Triggered when both
           local and R2 are empty (e.g. before the first sanity gate
           passes), or when the optional [nrfi] extras aren't installed.
        """
        cfg = config or get_default_config()

        def _attempt_load_from_local() -> object | None:
            try:
                from ..models.inference import NRFIInferenceEngine
                from ..models.model_training import MODEL_VERSION, TrainedBundle
                bundle = TrainedBundle.load(cfg.model_dir, MODEL_VERSION)
                return NRFIInferenceEngine(bundle, cfg)
            except Exception:
                return None

        # 1. Local cache
        engine = _attempt_load_from_local()
        if engine is not None:
            log.info("NRFIEngineBridge loaded local bundle from %s",
                     cfg.model_dir)
            return cls(cfg, engine)

        # 2. R2 fallback
        try:
            from edge_equation.utils.object_storage import (
                R2Client, download_latest_nrfi_bundle,
            )
            r2 = R2Client.from_env()
            if r2 is not None:
                fetched = download_latest_nrfi_bundle(r2, cfg.model_dir)
                if fetched is not None:
                    engine = _attempt_load_from_local()
                    if engine is not None:
                        log.info(
                            "NRFIEngineBridge loaded bundle from R2 "
                            "(latest.bundle) into %s", cfg.model_dir,
                        )
                        return cls(cfg, engine)
                    log.warning(
                        "NRFIEngineBridge: downloaded R2 bundle but "
                        "TrainedBundle.load() failed — falling back to baseline",
                    )
                else:
                    log.info(
                        "NRFIEngineBridge: R2 latest.bundle not found yet — "
                        "first sanity gate may not have passed",
                    )
        except Exception as e:
            log.info("NRFIEngineBridge: R2 fetch path skipped (%s)",
                     type(e).__name__)

        # 3. Baseline
        log.info(
            "NRFIEngineBridge: no trained bundle available — "
            "deterministic Poisson baseline will be used.",
        )
        return cls(cfg, None)

    def available(self) -> bool:
        """True iff the trained ML bundle is loaded (vs. baseline only)."""
        return self._loaded

    # -- Prediction -------------------------------------------------------
    def predict_for_features(
        self,
        feature_dicts: Sequence[Mapping[str, float]],
        *,
        game_ids: Sequence[str],
        market_probs: Optional[Sequence[Optional[float]]] = None,
        american_odds: Optional[Sequence[float]] = None,
        pitcher_bf_each: Optional[Sequence[float]] = None,
    ) -> list[NRFIBridgeOutput]:
        """Run the full pipeline and return bridge outputs.

        Falls back to the deterministic Poisson baseline (already
        computed inside the feature dict as `poisson_p_nrfi`) when the
        ML engine isn't loaded.
        """
        from .grading import grade_for_blended

        if not feature_dicts:
            return []

        # ------------------------------------------------------------------
        # Stage 1: get raw blended NRFI probabilities + λ.
        # ------------------------------------------------------------------
        if self._engine is not None:
            preds = self._engine.predict_many(
                feature_dicts,
                game_pks=list(range(len(feature_dicts))),  # placeholder; real game_ids attached below
                market_probs=market_probs,
                american_odds=american_odds,
            )
            raw_probs = [p.blended_p_nrfi for p in preds]
            lambdas = [p.lambda_total for p in preds]
            colors = [(p.color_band, p.color_hex, p.signal) for p in preds]
            shap_lists = [p.shap_drivers for p in preds]
            mc_bands = [(p.mc_low, p.mc_high) for p in preds]
        else:
            from ..utils.colors import gradient_hex, nrfi_band
            raw_probs = [float(fd.get("poisson_p_nrfi", 0.55)) for fd in feature_dicts]
            lambdas = [float(fd.get("lambda_total", 1.0)) for fd in feature_dicts]
            colors = []
            for p in raw_probs:
                b = nrfi_band(p * 100.0)
                colors.append((b.label, gradient_hex(p * 100.0), b.signal))
            shap_lists = [[] for _ in raw_probs]
            mc_bands = [(None, None) for _ in raw_probs]

        market_probs = list(market_probs) if market_probs is not None else [None] * len(raw_probs)
        bf_each = list(pitcher_bf_each) if pitcher_bf_each is not None else [0.0] * len(raw_probs)

        # ------------------------------------------------------------------
        # Stage 2: emit NRFI + complementary YRFI rows so downstream
        # markets can be priced by the deterministic engine.
        # ------------------------------------------------------------------
        out: list[NRFIBridgeOutput] = []
        for i, gid in enumerate(game_ids):
            blended = float(raw_probs[i])
            band, hexcol, signal = colors[i]
            mc_low, mc_high = mc_bands[i]

            # NRFI side
            mp = market_probs[i] if i < len(market_probs) else None
            grade = grade_for_blended(
                blended,
                market_implied_p=mp if mp is not None else 0.524,
                pitcher_batters_faced=bf_each[i],
            )
            out.append(NRFIBridgeOutput(
                game_id=str(gid),
                market_type="NRFI",
                selection="NRFI",
                fair_prob=Decimal(str(blended)).quantize(Decimal("0.000001")),
                nrfi_pct=round(blended * 100.0, 1),
                lambda_total=lambdas[i],
                color_band=band,
                color_hex=hexcol,
                signal=signal,
                grade=grade.grade,
                realization=grade.realization,
                edge=grade.edge if mp is not None else None,
                market_prob=mp,
                mc_low=mc_low, mc_high=mc_high,
                shap_drivers=shap_lists[i],
                metadata={"engine": "ml" if self._engine else "poisson_baseline"},
            ))

            # YRFI side (complementary probability — do NOT just flip,
            # use the same blended estimate so the market_implied edge
            # is symmetric).
            yrfi_p = 1.0 - blended
            mp_y = (1.0 - mp) if mp is not None else None
            grade_y = grade_for_blended(
                yrfi_p,
                market_implied_p=mp_y if mp_y is not None else 0.524,
                pitcher_batters_faced=bf_each[i],
            )
            out.append(NRFIBridgeOutput(
                game_id=str(gid),
                market_type="YRFI",
                selection="YRFI",
                fair_prob=Decimal(str(yrfi_p)).quantize(Decimal("0.000001")),
                nrfi_pct=round(yrfi_p * 100.0, 1),
                lambda_total=lambdas[i],
                color_band=band,        # color of game (one band per matchup)
                color_hex=hexcol,
                signal=signal,
                grade=grade_y.grade,
                realization=grade_y.realization,
                edge=grade_y.edge if mp_y is not None else None,
                market_prob=mp_y,
                mc_low=(1 - mc_high) if mc_high is not None else None,
                mc_high=(1 - mc_low) if mc_low is not None else None,
                shap_drivers=[(n, -v) for n, v in shap_lists[i]],
                metadata={"engine": "ml" if self._engine else "poisson_baseline"},
            ))
        return out
