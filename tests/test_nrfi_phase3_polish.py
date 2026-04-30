"""Tests for the phase-3 polish layer.

Covers:
* `nrfi.output.payload`        — canonical NRFIOutput + adapters
* `nrfi.abs_2026.effects`      — per-matchup BB uplift + adaptation curve
* `nrfi.calibration`           — reliability_summary + RollingHoldoutCalibrator
* `nrfi.evaluation.backtest`   — summary_table_str helper

Pure-Python — no xgboost / lightgbm / shap / fastapi dependency. Lives
in tests/ (not tests_api/) so the slim CI workflow can run it.
"""

from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
# nrfi.abs_2026.effects
# ---------------------------------------------------------------------------

def test_abs_priors_match_audit_targets():
    from edge_equation.engines.nrfi.abs_2026 import ABS_2026_PRIORS, PRE_ABS_WALK_RATE_LEAGUE
    assert 0.50 <= ABS_2026_PRIORS["overturn_rate"] <= 0.58
    assert 0.60 <= ABS_2026_PRIORS["catcher_success"] <= 0.68
    assert 0.097 <= ABS_2026_PRIORS["walk_rate_league"] <= 0.102
    assert PRE_ABS_WALK_RATE_LEAGUE == 0.085


def test_bb_pct_uplift_zero_when_abs_off():
    from edge_equation.engines.nrfi.abs_2026 import ABSContext, bb_pct_uplift
    ctx = ABSContext(active=False)
    assert bb_pct_uplift(0.085, ctx) == 0.0


def test_bb_pct_uplift_positive_for_paint_corners_pitcher():
    """High-CSW%, low-zone% pitcher should see a larger uplift than league avg."""
    from edge_equation.engines.nrfi.abs_2026 import ABSContext, bb_pct_uplift
    league = ABSContext(active=True, pitcher_csw_pct=0.295, pitcher_zone_pct=0.495)
    paint  = ABSContext(active=True, pitcher_csw_pct=0.330, pitcher_zone_pct=0.470)
    base = bb_pct_uplift(0.085, league)
    paint_uplift = bb_pct_uplift(0.085, paint)
    # Paint-corner pitcher must be ≥40% larger than league baseline uplift.
    assert paint_uplift > base * 1.4
    assert paint_uplift > 0.0


def test_bb_pct_uplift_smaller_for_pure_stuff_pitcher():
    """Low-CSW%, high-zone% pitcher (overpowering stuff) should see less."""
    from edge_equation.engines.nrfi.abs_2026 import ABSContext, bb_pct_uplift
    stuff = ABSContext(active=True, pitcher_csw_pct=0.260, pitcher_zone_pct=0.520)
    out = bb_pct_uplift(0.085, stuff)
    assert 0.0 < out < 0.014


def test_umpire_adaptation_curve_decays():
    from edge_equation.engines.nrfi.abs_2026 import umpire_adaptation_curve
    early = umpire_adaptation_curve(0)
    mid   = umpire_adaptation_curve(30)
    late  = umpire_adaptation_curve(120)
    assert early == 1.0
    assert 0.5 < late < mid < early
    # Asymptote
    assert umpire_adaptation_curve(10_000) >= 0.50


# ---------------------------------------------------------------------------
# nrfi.output.payload
# ---------------------------------------------------------------------------

def test_build_output_basic_fields():
    from edge_equation.engines.nrfi.output import build_output
    out = build_output(
        game_id="MLB-2026-04-27-NYY-BOS",
        blended_p=0.78,
        lambda_total=0.50,
        market_type="NRFI",
    )
    assert out.market_type == "NRFI"
    assert out.nrfi_pct == 78.0
    assert out.color_band == "Deep Green"
    assert out.signal == "STRONG_NRFI"
    assert out.tier == "ELITE"
    assert out.tier_band == "70-100%"
    assert out.headline().startswith("78.")


def test_build_output_yrfi_inverts_probability():
    from edge_equation.engines.nrfi.output import build_output
    out = build_output(
        game_id="g1", blended_p=0.30, lambda_total=2.40, market_type="YRFI",
    )
    # YRFI side at 0.70, deep green
    assert out.nrfi_pct == 70.0
    assert out.signal == "STRONG_NRFI"   # signal applies to the side shown


def test_build_output_kelly_only_when_market_present():
    from edge_equation.engines.nrfi.output import build_output
    no_market = build_output(game_id="g1", blended_p=0.78, lambda_total=0.50)
    with_market = build_output(game_id="g1", blended_p=0.78, lambda_total=0.50,
                                market_american_odds=-110)
    assert no_market.edge_pp is None and no_market.kelly_units is None
    assert with_market.edge_pp is not None
    # 0.78 vs implied 0.524 → after vig buffer >= 4% min edge → stake > 0
    assert with_market.kelly_units > 0


def test_build_output_mc_band_pp_computed():
    from edge_equation.engines.nrfi.output import build_output
    out = build_output(game_id="g1", blended_p=0.65, lambda_total=0.86,
                       mc_low=0.60, mc_high=0.71)
    # +/- ~5.5pp around the 65% point estimate
    assert out.mc_band_pp is not None
    assert 5.0 <= out.mc_band_pp <= 6.0


def test_to_email_card_renders_one_liner_plus_drivers():
    from edge_equation.engines.nrfi.output import build_output, to_email_card
    out = build_output(
        game_id="g1", blended_p=0.74, lambda_total=0.62,
        shap_drivers=[("home_p_xera", -0.08), ("park_factor_runs", -0.03),
                      ("ump_zone_idx", 0.02)],
        mc_low=0.69, mc_high=0.78,
    )
    s = to_email_card(out)
    assert "74.0% NRFI" in s
    assert "ELITE" in s
    assert "Deep Green" in s
    assert "drivers:" in s


def test_to_api_dict_serialises_drivers_as_pairs():
    from edge_equation.engines.nrfi.output import build_output, to_api_dict
    out = build_output(game_id="g1", blended_p=0.62, lambda_total=1.0,
                       shap_drivers=[("k_pct", 0.03)])
    d = to_api_dict(out)
    assert isinstance(d["shap_drivers"], list)
    assert d["shap_drivers"][0] == ["k_pct", 0.03]
    assert d["headline"]


def test_to_dashboard_row_has_preformatted_strings():
    from edge_equation.engines.nrfi.output import build_output, to_dashboard_row
    out = build_output(game_id="g1", blended_p=0.62, lambda_total=1.0,
                       market_american_odds=-115, mc_low=0.58, mc_high=0.66)
    row = to_dashboard_row(out)
    assert row["NRFI %"] == "62.0%"
    assert row["Tier"] == "MODERATE"
    assert row["MC ±"].startswith("±")
    assert "u" in row["Kelly"] or row["Kelly"] in {"Pass", "No bet"}


def test_build_output_includes_elite_daily_fields():
    from edge_equation.engines.nrfi.output import build_output, to_api_dict
    out = build_output(
        game_id="g1", blended_p=0.71, lambda_total=0.52,
        market_american_odds=-110, mc_low=0.67, mc_high=0.75,
        shap_drivers=[("away_top3_xwoba", 0.04), ("park_factor_runs", -0.02)],
    )
    d = to_api_dict(out)
    assert d["tier"] == "ELITE"
    assert d["tier_basis"] == "raw_probability"
    assert d["tier_band"] == "70-100%"
    assert d["mc_band_pp"] == 4.0
    assert d["edge_pp"] is not None
    assert d["kelly_suggestion"].endswith("u")
    assert d["driver_text"]


# ---------------------------------------------------------------------------
# nrfi.calibration
# ---------------------------------------------------------------------------

def test_reliability_summary_returns_n_bins_with_hits():
    from edge_equation.engines.nrfi.calibration import reliability_summary
    # Stay strictly inside the 70-80 bin; 0.80 sits on the next bin's
    # lower edge under the [lo, hi) convention so we avoid that exact value.
    probs   = [0.72, 0.74, 0.76, 0.78, 0.79] * 10
    actuals = [1, 1, 0, 1, 1] * 10
    bins = reliability_summary(probs, actuals, n_bins=10)
    assert len(bins) == 10
    bucket = next(b for b in bins if abs(b.lo - 0.7) < 1e-6)
    assert bucket.n == 50
    assert 0.7 <= bucket.actual <= 0.85


def test_reliability_summary_line_format():
    from edge_equation.engines.nrfi.calibration import BinSummary
    b = BinSummary(lo=0.7, hi=0.8, pred_mean=0.74, actual=0.72, n=50)
    s = b.line()
    assert "70.0%" in s and "80.0%" in s
    # Numeric formatting uses {:5.1f} so spacing varies; check substrings.
    assert "pred" in s and "74.0%" in s
    assert "actual" in s and "72.0%" in s
    assert "n=50" in s


def test_rolling_holdout_calibrator_passthrough_until_seeded():
    from edge_equation.engines.nrfi.calibration import RollingHoldoutCalibrator
    cal = RollingHoldoutCalibrator(window_size=200)
    assert cal.fitted is False
    # Before any data, transform is identity.
    assert cal.transform(0.62) == 0.62


def test_rolling_holdout_calibrator_refit_with_data():
    pytest = __import__("pytest")
    pytest.importorskip("sklearn")  # Calibrator uses sklearn isotonic.
    from edge_equation.engines.nrfi.calibration import RollingHoldoutCalibrator
    cal = RollingHoldoutCalibrator(window_size=300, method="isotonic")
    # Generate a clean monotone signal: y deterministically related to p.
    probs = [i / 200.0 for i in range(200)]
    actuals = [1 if p > 0.5 else 0 for p in probs]
    cal.add_observations(probs, actuals)
    assert cal.refit() is True
    assert cal.fitted is True
    # After refit, transform of values above 0.5 should be near 1.0
    assert cal.transform(0.95) > 0.85
    assert cal.transform(0.05) < 0.15


# ---------------------------------------------------------------------------
# nrfi.evaluation.backtest.summary_table_str
# ---------------------------------------------------------------------------

def _fake_report(n=100, base_rate=0.55, accuracy=0.62,
                  brier=0.21, log_loss=0.62):
    from edge_equation.engines.nrfi.evaluation.backtest import BacktestReport, RegimeMetrics
    df = pd.DataFrame({
        "game_pk": list(range(n)),
        "game_date": ["2026-04-27"] * n,
        "p_nrfi": [0.55 + (i % 30) / 100 for i in range(n)],
        "actual_nrfi": [1 if i % 2 == 0 else 0 for i in range(n)],
        "first_inn_runs": [i % 3 for i in range(n)],
    })
    return BacktestReport(
        n_games=n,
        brier=brier, log_loss=log_loss,
        accuracy=accuracy, base_rate=base_rate,
        reliability={"edges": [0.0, 0.5, 1.0], "predicted": [0.4, 0.7],
                     "actual": [0.4, 0.6], "count": [n // 2, n // 2]},
        regimes=[
            RegimeMetrics("pre_abs_2024_2025", n // 2, brier+0.005, log_loss+0.01,
                           accuracy-0.02, base_rate),
            RegimeMetrics("abs_2026_plus", n // 2, brier-0.003, log_loss-0.01,
                           accuracy+0.02, base_rate),
        ],
        per_game=df,
    )


def test_summary_table_str_renders_all_sections():
    from edge_equation.engines.nrfi.evaluation.backtest import summary_table_str
    s = summary_table_str(_fake_report(n=200))
    assert "Backtest summary" in s
    assert "N games" in s
    assert "Pre-ABS" in s
    assert "ABS-era" in s
    assert "Insights:" in s
    assert "Engine accuracy ABS-era" in s


def test_summary_table_str_handles_no_market_data():
    from edge_equation.engines.nrfi.evaluation.backtest import summary_table_str
    rep = _fake_report()
    rep.roi_flat = None
    s = summary_table_str(rep)
    assert "ROI" not in s.split("Insights:")[0]  # no ROI line in headline section
