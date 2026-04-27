"""Tests for the NRFI integration phase.

These tests are deliberately decoupled from the optional `[nrfi]` extras:
heavy ML deps (xgboost, lightgbm, shap) are mocked out so the wiring
itself can be exercised in CI without installing 200MB of binary
wheels. Pure-Python paths (shrinkage math, posting renderer, regime
slicing, color band mapping) get real coverage.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal


# ---------------------------------------------------------------------------
# nrfi.integration.shrinkage  (pure math)
# ---------------------------------------------------------------------------

def test_tango_shrink_zero_sample_returns_prior():
    from edge_equation.engines.nrfi.integration.shrinkage import tango_shrink
    out = tango_shrink(observed=0.500, n_observed=0,
                        prior_mean=0.318, n_prior=200)
    assert abs(out - 0.318) < 1e-9


def test_tango_shrink_large_sample_returns_observed():
    from edge_equation.engines.nrfi.integration.shrinkage import tango_shrink
    out = tango_shrink(observed=0.500, n_observed=10000,
                        prior_mean=0.318, n_prior=200)
    assert abs(out - 0.500) < 0.01  # within 1pp of observed


def test_top_of_order_shrink_uses_obp_prior():
    from edge_equation.engines.nrfi.integration.shrinkage import top_of_order_shrink
    # 200 PA observed, 200 prior PA → 50/50 blend.
    out = top_of_order_shrink(top3_obp=0.400, top3_pa=200.0)
    assert 0.355 < out < 0.365


def test_pitcher_era_shrink_regresses_extreme():
    from edge_equation.engines.nrfi.integration.shrinkage import shrink_pitcher_era
    # Tiny sample: 10 BF of 1.50 ERA → should snap nearly all the way back.
    out = shrink_pitcher_era(era=1.50, batters_faced=10)
    assert out > 4.0  # league-mean dominates


# ---------------------------------------------------------------------------
# nrfi.evaluation.backtest  (regime split, ABS toggle)
# ---------------------------------------------------------------------------

def test_abs_active_regime_switch():
    from edge_equation.engines.nrfi.evaluation.backtest import _abs_active_for_season
    assert _abs_active_for_season(2024) is False
    assert _abs_active_for_season(2025) is False
    assert _abs_active_for_season(2026) is True
    assert _abs_active_for_season(2027) is True


def test_season_from_date():
    from edge_equation.engines.nrfi.evaluation.backtest import _season_from_date
    assert _season_from_date("2024-04-15") == 2024
    assert _season_from_date("2026-09-30") == 2026


# ---------------------------------------------------------------------------
# src/edge_equation/posting/nrfi_card.py  (renderer)
# ---------------------------------------------------------------------------

def test_render_nrfi_section_filters_below_threshold():
    from edge_equation.posting.nrfi_card import render_nrfi_section
    picks = [
        {"game_id": "g1", "market_type": "NRFI", "selection": "NRFI",
         "nrfi_pct": 72.5, "color_band": "Deep Green",
         "lambda_total": 0.65, "edge": 0.08, "kelly_units": 1.4,
         "shap_drivers": [("home_p_xera", -0.05), ("park_factor_runs", -0.02)]},
        {"game_id": "g2", "market_type": "NRFI", "selection": "NRFI",
         "nrfi_pct": 50.5, "color_band": "Yellow",
         "lambda_total": 1.20, "edge": None, "kelly_units": None,
         "shap_drivers": []},
    ]
    text = render_nrfi_section(picks, min_nrfi_pct=60.0)
    assert "FIRST-INNING SIGNAL" in text
    assert "Facts. Not Feelings." in text
    assert "g1" in text
    assert "g2" not in text   # below threshold
    assert "72.5%" in text
    assert "DEEP_GREEN" in text


def test_render_nrfi_section_returns_empty_when_no_picks():
    from edge_equation.posting.nrfi_card import render_nrfi_section
    assert render_nrfi_section([]) == ""
    assert render_nrfi_section(
        [{"market_type": "NRFI", "nrfi_pct": 50.0}]
    ) == ""


def test_dashboard_payload_shape():
    from edge_equation.posting.nrfi_card import render_nrfi_dashboard_payload
    picks = [{"market_type": "NRFI", "nrfi_pct": 70.0},
             {"market_type": "YRFI", "nrfi_pct": 65.0}]
    out = render_nrfi_dashboard_payload(picks)
    assert out["meta"]["count"] == 2
    assert 60 < out["meta"]["min_pct"] < 71


# ---------------------------------------------------------------------------
# nrfi.utils.colors  (band mapping for the dashboard)
# ---------------------------------------------------------------------------

def test_color_bands():
    from edge_equation.engines.nrfi.utils.colors import nrfi_band, gradient_hex
    assert nrfi_band(15.0).signal == "STRONG_YRFI"
    assert nrfi_band(38.0).signal == "LEAN_YRFI"
    assert nrfi_band(50.0).signal == "COIN_FLIP"
    assert nrfi_band(62.0).signal == "LEAN_NRFI"
    assert nrfi_band(80.0).signal == "STRONG_NRFI"
    # Continuous gradient is well-formed across [0,100].
    for p in (0.0, 25.0, 50.0, 75.0, 100.0):
        h = gradient_hex(p)
        assert h.startswith("#") and len(h) == 7


# ---------------------------------------------------------------------------
# source_factory.nrfi_source_for_league  (additive registration)
# ---------------------------------------------------------------------------

def test_nrfi_source_for_league_only_supports_mlb():
    from edge_equation.ingestion.source_factory import nrfi_source_for_league
    assert nrfi_source_for_league("KBO") is None
    assert nrfi_source_for_league("NPB") is None
    src = nrfi_source_for_league("MLB")
    assert src is not None
    assert src.league == "MLB"


# ---------------------------------------------------------------------------
# mlb_nrfi_source — exercises the bridge with the engine disabled so we
# only validate the wire format. Mock NRFIEngineBridge to avoid pulling in
# xgboost/shap.
# ---------------------------------------------------------------------------

class _FakeBridgeOutput:
    def __init__(self, game_id, market_type):
        from decimal import Decimal as _D
        self.game_id = game_id
        self.market_type = market_type
        self.selection = market_type
        self.fair_prob = _D("0.6500") if market_type == "NRFI" else _D("0.3500")
        self.nrfi_pct = 65.0 if market_type == "NRFI" else 35.0
        self.lambda_total = 0.86
        self.color_band = "Light Green" if market_type == "NRFI" else "Orange"
        self.color_hex = "#7cb342"
        self.signal = "LEAN_NRFI" if market_type == "NRFI" else "LEAN_YRFI"
        self.grade = "B"
        self.realization = 60
        self.edge = None
        self.kelly = None
        self.market_prob = None
        self.mc_low = None
        self.mc_high = None
        self.shap_drivers = []
        self.metadata = {"engine": "fake_test_bridge"}


class _FakeBridge:
    def predict_for_features(self, feature_dicts, *, game_ids,
                              market_probs=None, american_odds=None,
                              pitcher_bf_each=None):
        out = []
        for gid in game_ids:
            out.append(_FakeBridgeOutput(gid, "NRFI"))
            out.append(_FakeBridgeOutput(gid, "YRFI"))
        return out


def test_mlb_nrfi_source_emits_two_markets_per_game(monkeypatch):
    from edge_equation.ingestion.mlb_nrfi_source import MLBNRFISource
    src = MLBNRFISource()
    # Inject the fake bridge so we don't need xgboost.
    src._bridge = _FakeBridge()
    markets = src.get_raw_markets(datetime(2026, 4, 27, 13, 0, 0))
    assert markets, "expected non-empty market list"
    nrfi = [m for m in markets if m["market_type"] == "NRFI"]
    yrfi = [m for m in markets if m["market_type"] == "YRFI"]
    assert len(nrfi) == len(yrfi) > 0
    # Each row has the deterministic-engine contract (home_lambda + away_lambda).
    for m in markets:
        assert "home_lambda" in m["meta"]["inputs"]
        assert "away_lambda" in m["meta"]["inputs"]
        assert m["meta"]["nrfi_engine"]["color_band"] in ("Light Green", "Orange")


# NOTE: API-route tests live in tests_api/test_nrfi_router.py because they
# require fastapi, which is not in the [dev] extras the `Tests` workflow
# installs. Keeping this file fastapi-free preserves the CI fast path.


# ---------------------------------------------------------------------------
# nrfi.integration.grading  (sample-size cap)
# ---------------------------------------------------------------------------

def test_grade_caps_at_C_on_low_sample():
    from edge_equation.engines.nrfi.integration.grading import grade_for_blended
    # Strong nominal edge but only 30 BF of pitcher data — must cap at C.
    out = grade_for_blended(blended_p=0.78, market_implied_p=0.524,
                              pitcher_batters_faced=30.0)
    assert out.grade == "C"


def test_grade_uses_full_grade_with_ample_sample():
    from edge_equation.engines.nrfi.integration.grading import grade_for_blended
    out = grade_for_blended(blended_p=0.78, market_implied_p=0.524,
                              pitcher_batters_faced=400.0)
    # With +25pp edge and 400 BF sample we expect at least a B (cap rule passes).
    assert out.grade in ("A+", "A", "B")
