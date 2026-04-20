from decimal import Decimal
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer
from edge_equation.config.sport_config import SPORT_CONFIG
from edge_equation.math.stats import DeterministicStats


def test_ml_example_formula_consistency():
    sport = "MLB"
    cfg = SPORT_CONFIG[sport]
    ml_weight = cfg["ml_universal_weight"]

    inputs = {
        "strength_home": 1.32,
        "strength_away": 1.15,
        "home_adv": 0.115,
    }
    universal = {"home_edge": 0.085}

    result = ProbabilityCalculator.calculate_fair_value("ML", sport, inputs, universal)
    fair_prob = result["fair_prob"]

    # Recompute expected using the same formula
    from edge_equation.math.probability import ProbabilityCalculator as PC
    base_prob = PC.bradley_terry(
        inputs["strength_home"],
        inputs["strength_away"],
        inputs["home_adv"],
    )
    raw_univ = DeterministicStats.compute_universal_sum(universal)
    clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
    expected = base_prob + clamped_univ * ml_weight
    if expected < Decimal('0.01'):
        expected = Decimal('0.01')
    if expected > Decimal('0.99'):
        expected = Decimal('0.99')
    expected = expected.quantize(Decimal('0.000001'))

    assert fair_prob == expected


def test_totals_example_formula_consistency():
    sport = "MLB"
    cfg = SPORT_CONFIG[sport]
    baseline = cfg["league_baseline_total"]

    inputs = {
        "off_env": 1.18,
        "def_env": 1.07,
        "pace": 1.03,
        "dixon_coles_adj": 0.00,
    }

    result = ProbabilityCalculator.calculate_fair_value("Total", sport, inputs, {})
    total = result["expected_total"]

    env_factor = Decimal(str(inputs["off_env"])) * Decimal(str(inputs["def_env"])) * Decimal(str(inputs["pace"]))
    expected = (baseline * env_factor + Decimal(str(inputs["dixon_coles_adj"]))).quantize(Decimal('0.01'))

    assert total == expected


def test_prop_examples_formula_consistency():
    sport = "MLB"
    cfg = SPORT_CONFIG[sport]
    prop_weight = cfg["prop_universal_weight"]

    # HR
    hr_inputs = {"rate": 0.142}
    hr_univ = {"matchup_exploit": 0.08, "market_line_delta": 0.12}
    hr_result = ProbabilityCalculator.calculate_fair_value("HR", sport, hr_inputs, hr_univ)
    hr_raw_univ = DeterministicStats.compute_universal_sum(hr_univ)
    hr_weighted = hr_raw_univ * prop_weight
    hr_multiplier = DeterministicStats.clamp_prop_multiplier(hr_weighted)
    hr_expected = (Decimal(str(hr_inputs["rate"])) * hr_multiplier).quantize(Decimal('0.01'))
    assert hr_result["expected_value"] == hr_expected

    # K
    k_inputs = {"rate": 7.85}
    k_univ = {"matchup_exploit": 0.09, "market_line_delta": 0.08}
    k_result = ProbabilityCalculator.calculate_fair_value("K", sport, k_inputs, k_univ)
    k_raw_univ = DeterministicStats.compute_universal_sum(k_univ)
    k_weighted = k_raw_univ * prop_weight
    k_multiplier = DeterministicStats.clamp_prop_multiplier(k_weighted)
    k_expected = (Decimal(str(k_inputs["rate"])) * k_multiplier).quantize(Decimal('0.01'))
    assert k_result["expected_value"] == k_expected

    # Passing yards (NFL)
    sport_nfl = "NFL"
    cfg_nfl = SPORT_CONFIG[sport_nfl]
    prop_weight_nfl = cfg_nfl["prop_universal_weight"]
    py_inputs = {"rate": 312.4}
    py_univ = {"form_off": 0.11, "matchup_strength": 0.09}
    py_result = ProbabilityCalculator.calculate_fair_value("Passing_Yards", sport_nfl, py_inputs, py_univ)
    py_raw_univ = DeterministicStats.compute_universal_sum(py_univ)
    py_weighted = py_raw_univ * prop_weight_nfl
    py_multiplier = DeterministicStats.clamp_prop_multiplier(py_weighted)
    py_expected = (Decimal(str(py_inputs["rate"])) * py_multiplier).quantize(Decimal('0.01'))
    assert py_result["expected_value"] == py_expected

    # Rushing yards (NFL)
    ry_inputs = {"rate": 78.5}
    ry_univ = {"form_off": -0.04, "matchup_strength": -0.06}
    ry_result = ProbabilityCalculator.calculate_fair_value("Rushing_Yards", sport_nfl, ry_inputs, ry_univ)
    ry_raw_univ = DeterministicStats.compute_universal_sum(ry_univ)
    ry_weighted = ry_raw_univ * prop_weight_nfl
    ry_multiplier = DeterministicStats.clamp_prop_multiplier(ry_weighted)
    ry_expected = (Decimal(str(ry_inputs["rate"])) * ry_multiplier).quantize(Decimal('0.01'))
    assert ry_result["expected_value"] == ry_expected

    # Receiving yards (NFL)
    rec_inputs = {"rate": 92.3}
    rec_univ = {"form_off": 0.07}
    rec_result = ProbabilityCalculator.calculate_fair_value("Receiving_Yards", sport_nfl, rec_inputs, rec_univ)
    rec_raw_univ = DeterministicStats.compute_universal_sum(rec_univ)
    rec_weighted = rec_raw_univ * prop_weight_nfl
    rec_multiplier = DeterministicStats.clamp_prop_multiplier(rec_weighted)
    rec_expected = (Decimal(str(rec_inputs["rate"])) * rec_multiplier).quantize(Decimal('0.01'))
    assert rec_result["expected_value"] == rec_expected

    # NCAA points
    sport_ncaa = "NCAA_Basketball"
    cfg_ncaa = SPORT_CONFIG[sport_ncaa]
    prop_weight_ncaa = cfg_ncaa["prop_universal_weight"]
    pts_inputs = {"rate": 18.7}
    pts_univ = {"form_off": 0.12}
    pts_result = ProbabilityCalculator.calculate_fair_value("Points", sport_ncaa, pts_inputs, pts_univ)
    pts_raw_univ = DeterministicStats.compute_universal_sum(pts_univ)
    pts_weighted = pts_raw_univ * prop_weight_ncaa
    pts_multiplier = DeterministicStats.clamp_prop_multiplier(pts_weighted)
    pts_expected = (Decimal(str(pts_inputs["rate"])) * pts_multiplier).quantize(Decimal('0.01'))
    assert pts_result["expected_value"] == pts_expected

    # NCAA rebounds
    reb_inputs = {"rate": 8.9}
    reb_univ = {"form_def": -0.05}
    reb_result = ProbabilityCalculator.calculate_fair_value("Rebounds", sport_ncaa, reb_inputs, reb_univ)
    reb_raw_univ = DeterministicStats.compute_universal_sum(reb_univ)
    reb_weighted = reb_raw_univ * prop_weight_ncaa
    reb_multiplier = DeterministicStats.clamp_prop_multiplier(reb_weighted)
    reb_expected = (Decimal(str(reb_inputs["rate"])) * reb_multiplier).quantize(Decimal('0.01'))
    assert reb_result["expected_value"] == reb_expected

    # NCAA assists
    ast_inputs = {"rate": 6.2}
    ast_univ = {"form_off": 0.06}
    ast_result = ProbabilityCalculator.calculate_fair_value("Assists", sport_ncaa, ast_inputs, ast_univ)
    ast_raw_univ = DeterministicStats.compute_universal_sum(ast_univ)
    ast_weighted = ast_raw_univ * prop_weight_ncaa
    ast_multiplier = DeterministicStats.clamp_prop_multiplier(ast_weighted)
    ast_expected = (Decimal(str(ast_inputs["rate"])) * ast_multiplier).quantize(Decimal('0.01'))
    assert ast_result["expected_value"] == ast_expected

    # NHL SOG
    sport_nhl = "NHL"
    cfg_nhl = SPORT_CONFIG[sport_nhl]
    prop_weight_nhl = cfg_nhl["prop_universal_weight"]
    sog_inputs = {"rate": 4.12}
    sog_univ = {"matchup_exploit": 0.10}
    sog_result = ProbabilityCalculator.calculate_fair_value("SOG", sport_nhl, sog_inputs, sog_univ)
    sog_raw_univ = DeterministicStats.compute_universal_sum(sog_univ)
    sog_weighted = sog_raw_univ * prop_weight_nhl
    sog_multiplier = DeterministicStats.clamp_prop_multiplier(sog_weighted)
    sog_expected = (Decimal(str(sog_inputs["rate"])) * sog_multiplier).quantize(Decimal('0.01'))
    assert sog_result["expected_value"] == sog_expected


def test_edge_kelly_and_grade_consistency():
    # Use the ML example fair_prob as computed by the engine
    sport = "MLB"
    inputs = {
        "strength_home": 1.32,
        "strength_away": 1.15,
        "home_adv": 0.115,
    }
    universal = {"home_edge": 0.085}
    fv = ProbabilityCalculator.calculate_fair_value("ML", sport, inputs, universal)
    fair_prob = fv["fair_prob"]

    odds = -132
    edge = EVCalculator.calculate_edge(fair_prob, odds)
    dec_odds = EVCalculator.american_to_decimal(odds)
    kelly_full = EVCalculator.kelly_fraction(edge, dec_odds)
    kelly_half = (kelly_full / Decimal('2')).quantize(Decimal('0.0001'))

    # Calibrate wrapper should match this logic (with B-grade threshold)
    calib = EVCalculator.calibrate(False, {"fair_prob": fair_prob}, {"odds": odds})
    assert calib["edge"] == edge
    if edge >= Decimal('0.010000'):
        assert calib["kelly"] == kelly_half
    else:
        assert calib["kelly"] == Decimal('0')

    grade = ConfidenceScorer.grade(edge)
    # Just assert grade is one of the allowed buckets
    assert grade in ("A+", "A", "B", "C")
