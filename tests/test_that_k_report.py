"""
That K Report -- unit + integration tests.

Covers:
  1. Each multiplicative adjustment isolates correctly (only the
     factor under test moves the projected mean away from baseline).
  2. 5k Monte Carlo is deterministic for a given seed_key and
     produces a reasonable distribution.
  3. ConfidenceScorer grades map onto the probability-edge ladder.
  4. render_report obeys the exact output format the brief requires.
  5. The sample-slate + runner dry-run path completes cleanly and
     emits a usable text report.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from edge_equation.that_k import (
    GameContext,
    OpponentLineup,
    PitcherProfile,
    build_projections,
    project_strikeouts,
    render_report,
    simulate_strikeouts,
)
from edge_equation.that_k.model import (
    LEAGUE_CSW,
    LEAGUE_K_PER_BF,
    LEAGUE_SWSTR,
)
from edge_equation.that_k.report import DEFAULT_TOP_N, grade_row
from edge_equation.that_k.runner import _pitcher_from_row
from edge_equation.that_k.sample_slate import sample_slate
from edge_equation.that_k.simulator import DEFAULT_N_SIMS, _nb_sample
import random


# ------------------------------------------------ model factor isolation

def _baseline_pitcher(**overrides) -> PitcherProfile:
    defaults = dict(
        name="Test Pitcher", team="XYZ", throws="R",
        k_per_bf=LEAGUE_K_PER_BF, expected_bf=24.0,
        arsenal={},
        # >=3 recent starts so project_strikeouts does NOT widen the
        # NB dispersion via the sample_warning path.  Tests that need
        # the thin-history case override this explicitly.
        recent_k_per_bf=[
            (LEAGUE_K_PER_BF, 2),
            (LEAGUE_K_PER_BF, 8),
            (LEAGUE_K_PER_BF, 15),
        ],
    )
    defaults.update(overrides)
    return PitcherProfile(**defaults)


def _neutral_lineup(**overrides) -> OpponentLineup:
    defaults = dict(
        team="ABC", swstr_pct=LEAGUE_SWSTR, csw_pct=LEAGUE_CSW,
        lhh_share=0.5,
    )
    defaults.update(overrides)
    return OpponentLineup(**defaults)


def _neutral_ctx(**overrides) -> GameContext:
    defaults = dict(
        dome=True, umpire_k_factor=1.0, park_k_factor=1.0,
    )
    defaults.update(overrides)
    return GameContext(**defaults)


def test_neutral_inputs_yield_baseline_mean():
    inp = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    assert inp.total_adj == pytest.approx(1.0, abs=0.01)
    assert inp.projected_mean == pytest.approx(
        LEAGUE_K_PER_BF * 24.0, abs=0.05,
    )


def test_high_swstr_lineup_boosts_projection():
    base = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    high = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(swstr_pct=LEAGUE_SWSTR + 0.020),
        _neutral_ctx(),
    )
    assert high.projected_mean > base.projected_mean


def test_k_positive_umpire_boosts_projection():
    base = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    plus = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(),
        _neutral_ctx(umpire_k_factor=1.08),
    )
    assert plus.projected_mean > base.projected_mean


def test_cold_weather_boosts_projection_slightly():
    """Temp below 72F should marginally boost K's (ball carries less)."""
    base = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(),
        _neutral_ctx(dome=False, temp_f=72.0),
    )
    cold = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(),
        _neutral_ctx(dome=False, temp_f=50.0),
    )
    assert cold.projected_mean > base.projected_mean


def test_recent_hot_streak_boosts_projection():
    base = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    hot = project_strikeouts(
        _baseline_pitcher(recent_k_per_bf=[
            (LEAGUE_K_PER_BF + 0.05, 2),
            (LEAGUE_K_PER_BF + 0.04, 8),
            (LEAGUE_K_PER_BF + 0.06, 14),
        ]),
        _neutral_lineup(),
        _neutral_ctx(),
    )
    assert hot.projected_mean > base.projected_mean


def test_thin_recent_history_widens_dispersion():
    thin = project_strikeouts(
        _baseline_pitcher(recent_k_per_bf=[]),
        _neutral_lineup(), _neutral_ctx(),
    )
    full = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(), _neutral_ctx(),
    )
    assert thin.nb_dispersion < full.nb_dispersion
    assert thin.sample_warning is True
    assert full.sample_warning is False


def test_handedness_same_side_boosts_slightly():
    """L-on-L lineup vs LHP should get a small uplift; L-on-R suppresses."""
    lhp = _baseline_pitcher(throws="L")
    same_side = project_strikeouts(
        lhp, _neutral_lineup(lhh_share=0.70), _neutral_ctx(),
    )
    opposite = project_strikeouts(
        lhp, _neutral_lineup(lhh_share=0.30), _neutral_ctx(),
    )
    assert same_side.projected_mean > opposite.projected_mean


def test_total_adjustment_clamped_under_extreme_inputs():
    """Extreme lineup + extreme umpire must not blow the multiplier
    past +/- 25%.  Protects against the projector ever shipping a
    K projection doubled from baseline."""
    inp = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(swstr_pct=0.25, csw_pct=0.45),
        _neutral_ctx(umpire_k_factor=1.20),
    )
    assert inp.total_adj <= 1.25 + 1e-9


# ------------------------------------------------ MC simulator

def test_simulator_is_deterministic_from_seed():
    a = simulate_strikeouts(
        "P", "T", "O", line=7.5, mean=7.8,
        dispersion=10.0, seed_key="G1",
    )
    b = simulate_strikeouts(
        "P", "T", "O", line=7.5, mean=7.8,
        dispersion=10.0, seed_key="G1",
    )
    assert a.mean == b.mean
    assert a.prob_over == b.prob_over
    assert a.p10 == b.p10
    assert a.p90 == b.p90


def test_simulator_prob_over_above_half_when_mean_over_line():
    r = simulate_strikeouts(
        "P", "T", "O", line=6.5, mean=7.9,
        dispersion=10.0, seed_key="G-over",
    )
    assert r.prob_over > Decimal("0.50")
    assert r.lean == "over"


def test_simulator_prob_under_above_half_when_mean_below_line():
    r = simulate_strikeouts(
        "P", "T", "O", line=8.5, mean=6.5,
        dispersion=10.0, seed_key="G-under",
    )
    assert r.prob_under > Decimal("0.50")
    assert r.lean == "under"


def test_simulator_percentiles_are_ordered():
    r = simulate_strikeouts(
        "P", "T", "O", line=7.5, mean=8.0,
        dispersion=10.0, seed_key="G-pct",
    )
    assert r.p10 <= r.p50 <= r.p90


def test_nb_sample_variance_matches_spec():
    """Var ~ mean + mean^2/r within a reasonable band over 20k draws."""
    rng = random.Random(1234)
    mean = 8.0
    r = 10.0
    n = 20000
    samples = [_nb_sample(rng, mean, r) for _ in range(n)]
    emp_mean = sum(samples) / n
    emp_var = sum((x - emp_mean) ** 2 for x in samples) / (n - 1)
    expected_var = mean + mean ** 2 / r
    # Sampling tolerance: +/- 15% on variance across 20k draws is fine.
    assert emp_var == pytest.approx(expected_var, rel=0.15)


def test_default_n_sims_is_at_least_5000():
    assert DEFAULT_N_SIMS >= 5000


# ------------------------------------------------ grading

def test_grade_row_maps_edge_to_confidence_scorer():
    # Craft a projection with a known edge_prob magnitude.
    from edge_equation.that_k.simulator import KProjection
    proj = KProjection(
        pitcher="P", team="T", opponent="O",
        line=Decimal("7.5"),
        mean=Decimal("9.0"), stdev=Decimal("2.8"),
        p10=Decimal("6.0"), p50=Decimal("9.0"), p90=Decimal("12.0"),
        prob_over=Decimal("0.65"), prob_under=Decimal("0.35"),
        n_sims=5000,
        edge_ks=Decimal("1.5"),
        edge_prob=Decimal("0.15"),
        lean="over",
    )
    assert grade_row(proj) == "A+"  # 0.15 >= 0.08 A+ threshold


# ------------------------------------------------ render format

def _row_for_format():
    pitcher = PitcherProfile(
        name="Gerrit Cole", team="NYY", throws="R",
        k_per_bf=0.285, expected_bf=25,
        swstr_pct=0.135, csw_pct=0.315,
        arsenal={"FF": 0.11, "SL": 0.175},
        recent_k_per_bf=[(0.31, 3), (0.33, 9)],
    )
    lineup = OpponentLineup(
        team="BOS", swstr_pct=0.124, csw_pct=0.302,
        lhh_share=0.55, swstr_vs_R=0.124,
    )
    ctx = GameContext(
        dome=False, temp_f=62.0, wind_mph=11.0, wind_dir="out",
        umpire_name="D. Bellino", umpire_k_factor=1.06,
    )
    rows = build_projections([{
        "game_id": "G1", "line": 7.5,
        "pitcher": pitcher.to_dict(),
        "lineup": lineup.to_dict(),
        "context": ctx.to_dict(),
    }])
    return rows


def test_report_obeys_exact_output_format():
    rows = _row_for_format()
    text = render_report(rows, date_str="2026-04-23")
    # Header + brand lines are verbatim per the spec.
    assert text.startswith("That K Report — 2026-04-23\n")
    assert "Tonight's Pitcher K Projections" in text
    assert text.rstrip().endswith("Powered by Edge Equation")
    # Block structure for the pitcher.
    assert "• Gerrit Cole (NYY) vs. BOS" in text
    assert "  Line: 7.5" in text
    assert "  K Projection: " in text
    assert "  Grade: " in text
    assert "  Edge: " in text
    # No hype language anywhere in the rendered text.
    hype = [
        "take the over", "take the under", "lock",
        "smash", "cash it", "hammer", "slam dunk",
    ]
    lower = text.lower()
    for word in hype:
        assert word not in lower, f"hype word {word!r} leaked into report"


def test_report_caps_at_top_n():
    slate = sample_slate()
    rows = build_projections(slate)
    text = render_report(rows, date_str="2026-04-23", top_n=3)
    # Only three pitcher bullets render.
    bullet_count = text.count("\n• ")
    # "\n• " before each block plus one leading (check split).
    assert text.startswith("That K Report")
    assert len([ln for ln in text.splitlines() if ln.startswith("• ")]) == 3


def test_report_orders_by_edge_prob_desc():
    slate = sample_slate()
    rows = build_projections(slate)
    text = render_report(rows, date_str="2026-04-23", top_n=DEFAULT_TOP_N)
    # The first pitcher bullet must correspond to the row with the
    # highest edge_prob -- not the first in slate order.
    best = max(rows, key=lambda r: r.projection.edge_prob)
    first_bullet = next(ln for ln in text.splitlines() if ln.startswith("• "))
    assert best.pitcher.name in first_bullet


# ------------------------------------------------ sample slate dry-run

def test_sample_slate_dry_run_end_to_end():
    slate = sample_slate()
    rows = build_projections(slate)
    assert len(rows) == len(slate)
    text = render_report(rows, date_str="2026-04-23")
    # Every pitcher in the sample slate appears in the output.
    for row in rows[:DEFAULT_TOP_N]:
        assert row.pitcher.name in text
    # Report length stays bounded (8 starters * ~6 lines + header/footer).
    assert 30 <= text.count("\n") <= 100


def test_cli_sample_dry_run(capsys):
    """Smoke test the CLI entry as invoked by the daily workflow."""
    from edge_equation.that_k.__main__ import main
    rc = main(["--sample", "--date", "2026-04-23"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("That K Report — 2026-04-23\n")
    assert "Powered by Edge Equation" in out


# ------------------------------------------------ runner row parsing

def test_pitcher_row_parser_handles_missing_optional_fields():
    """Minimal row (no arsenal, no recent history) still builds."""
    p = _pitcher_from_row({
        "pitcher": {"name": "X", "team": "Y"},
    })
    assert p.name == "X"
    assert p.team == "Y"
    assert p.k_per_bf == LEAGUE_K_PER_BF
