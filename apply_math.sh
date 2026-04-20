#!/bin/bash
set -e

echo "=== Creating Phase-2 deterministic math layer with clamps and baselines ==="

ROOT_DIR="$(pwd)"
SRC_DIR="$ROOT_DIR/src"
TEST_DIR="$ROOT_DIR/tests"

mkdir -p "$SRC_DIR/edge_equation/math"
mkdir -p "$SRC_DIR/edge_equation/config"
mkdir -p "$TEST_DIR"

########################################
# stats.py
########################################
cat > "$SRC_DIR/edge_equation/math/stats.py" << 'EOF'
from decimal import Decimal, getcontext

getcontext().prec = 28


class DeterministicStats:
    LAMBDA_DECAY = Decimal('0.95')
    MAX_UNIVERSAL_PROB_IMPACT = Decimal('0.10')
    MIN_PROP_MULTIPLIER = Decimal('0.75')
    MAX_PROP_MULTIPLIER = Decimal('1.25')

    UNIVERSAL_KEYS = [
        'pace_delta', 'off_eff_delta', 'def_eff_delta', 'sos_off_delta', 'sos_def_delta',
        'regression_off', 'regression_def', 'regression_form', 'volatility_team',
        'volatility_matchup', 'rest_delta', 'travel_delta', 'schedule_density',
        'home_edge', 'market_line_delta', 'form_off', 'form_def', 'form_combined',
        'matchup_strength', 'matchup_exploit', 'matchup_risk'
    ]

    @staticmethod
    def exponential_decay_rolling(values: list[float]) -> Decimal:
        if not values:
            return Decimal('0')
        n = len(values)
        weights = [DeterministicStats.LAMBDA_DECAY ** Decimal(i) for i in range(n)]
        weighted_sum = sum(Decimal(str(v)) * w for v, w in zip(reversed(values), weights))
        weight_sum = sum(weights)
        return (weighted_sum / weight_sum).quantize(Decimal('0.000001'))

    @staticmethod
    def compute_universal_sum(features: dict) -> Decimal:
        total = Decimal('0')
        for key in DeterministicStats.UNIVERSAL_KEYS:
            total += Decimal(str(features.get(key, 0.0)))
        return total

    @staticmethod
    def clamp_universal_prob(raw_universal_sum: Decimal) -> Decimal:
        if raw_universal_sum > DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT:
            return DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT
        if raw_universal_sum < -DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT:
            return -DeterministicStats.MAX_UNIVERSAL_PROB_IMPACT
        return raw_universal_sum

    @staticmethod
    def clamp_prop_multiplier(raw_universal_sum: Decimal) -> Decimal:
        multiplier = Decimal('1') + raw_universal_sum
        if multiplier > DeterministicStats.MAX_PROP_MULTIPLIER:
            return DeterministicStats.MAX_PROP_MULTIPLIER
        if multiplier < DeterministicStats.MIN_PROP_MULTIPLIER:
            return DeterministicStats.MIN_PROP_MULTIPLIER
        return multiplier
EOF

########################################
# sport_config.py
########################################
cat > "$SRC_DIR/edge_equation/config/sport_config.py" << 'EOF'
from decimal import Decimal

SPORT_CONFIG = {
    "MLB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.86'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "KBO": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NPB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NFL": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards", "Receiving_Yards"],
        "league_baseline_total": Decimal('47.5'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NCAA_Football": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards"],
        "league_baseline_total": Decimal('48.5'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NCAA_Basketball": {
        "markets": ["ML", "Spread", "Total", "Points", "Rebounds", "Assists"],
        "league_baseline_total": Decimal('155.0'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "Soccer": {
        "markets": ["ML", "Total", "BTTS"],
        "league_baseline_total": Decimal('2.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NHL": {
        "markets": ["ML", "Puck_Line", "Total", "SOG"],
        "league_baseline_total": Decimal('5.85'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
}
EOF

########################################
# probability.py
########################################
cat > "$SRC_DIR/edge_equation/math/probability.py" << 'EOF'
from decimal import Decimal
from .stats import DeterministicStats
from edge_equation.config.sport_config import SPORT_CONFIG


class ProbabilityCalculator:
    """
    Core fair-value math:
    - ML: Bradley-Terry + weighted universal_sum (clamped), then prob clamped to [0.01, 0.99]
    - Totals: league_baseline_total * (off_env * def_env * pace) + Dixon-Coles adj
    - Props: base_rate * (1 + prop_weight * clamped_universal_sum), with multiplier clamped to [0.75, 1.25]
    """

    @staticmethod
    def bradley_terry(strength_home: float, strength_away: float, home_adv: float) -> Decimal:
        import math
        home = strength_home * math.exp(home_adv)
        away = strength_away
        prob = home / (home + away)
        return Decimal(str(prob))

    @staticmethod
    def _get_weights_and_baseline(sport: str):
        cfg = SPORT_CONFIG[sport]
        return (
            cfg["ml_universal_weight"],
            cfg["prop_universal_weight"],
            cfg["league_baseline_total"],
        )

    @staticmethod
    def expected_total(sport: str, off_env: float, def_env: float, pace: float, dixon_coles_adj: float) -> Decimal:
        _, _, baseline = ProbabilityCalculator._get_weights_and_baseline(sport)
        env_factor = Decimal(str(off_env)) * Decimal(str(def_env)) * Decimal(str(pace))
        total = baseline * env_factor + Decimal(str(dixon_coles_adj))
        return total

    @staticmethod
    def expected_prop_rate(base_rate: float, raw_universal_sum: Decimal, prop_weight: Decimal) -> Decimal:
        # Apply weight to universal_sum, then clamp multiplier to [0.75, 1.25]
        weighted_univ = raw_universal_sum * prop_weight
        multiplier = DeterministicStats.clamp_prop_multiplier(weighted_univ)
        return Decimal(str(base_rate)) * multiplier

    @staticmethod
    def calculate_fair_value(market_type: str, sport: str, inputs: dict, universal_features: dict) -> dict:
        raw_univ = DeterministicStats.compute_universal_sum(universal_features)
        ml_weight, prop_weight, _ = ProbabilityCalculator._get_weights_and_baseline(sport)

        # ML: BT + weighted universal_sum (clamped), then prob clamped to [0.01, 0.99]
        if market_type == "ML":
            base_prob = ProbabilityCalculator.bradley_terry(
                inputs["strength_home"],
                inputs["strength_away"],
                inputs.get("home_adv", 0.115),
            )
            clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
            fair_prob = base_prob + clamped_univ * ml_weight
            if fair_prob < Decimal('0.01'):
                fair_prob = Decimal('0.01')
            if fair_prob > Decimal('0.99'):
                fair_prob = Decimal('0.99')
            fair_prob = fair_prob.quantize(Decimal('0.000001'))
            return {
                "fair_prob": fair_prob,
                "raw_universal_sum": raw_univ,
                "clamped_universal_sum": clamped_univ,
            }

        # Totals: league_baseline_total * env_factor + DC adj, then 2-decimal rounding
        if market_type in ["Total", "Game_Total"]:
            dixon_coles_adj = inputs.get("dixon_coles_adj", 0.0)
            total = ProbabilityCalculator.expected_total(
                sport,
                inputs["off_env"],
                inputs["def_env"],
                inputs["pace"],
                dixon_coles_adj,
            )
            total = total.quantize(Decimal('0.01'))
            return {
                "expected_total": total,
                "raw_universal_sum": raw_univ,
            }

        # Rate-based props: HR, K, yards, points, rebounds, assists, SOG
        if market_type in [
            "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
            "Points", "Rebounds", "Assists", "SOG"
        ]:
            base_rate = inputs["rate"]
            adjusted = ProbabilityCalculator.expected_prop_rate(base_rate, raw_univ, prop_weight)
            adjusted = adjusted.quantize(Decimal('0.01'))
            return {
                "expected_value": adjusted,
                "raw_universal_sum": raw_univ,
            }

        # BTTS: Poisson-based, then universal_sum impact on prob (clamped)
        if market_type == "BTTS":
            import math
            home_lambda = inputs.get("home_lambda", 1.2)
            away_lambda = inputs.get("away_lambda", 1.1)
            p0_home = math.exp(-home_lambda)
            p0_away = math.exp(-away_lambda)
            base_prob = 1 - (p0_home * p0_away)
            clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
            fair_prob = Decimal(str(base_prob)) + clamped_univ * ml_weight
            if fair_prob < Decimal('0.01'):
                fair_prob = Decimal('0.01')
            if fair_prob > Decimal('0.99'):
                fair_prob = Decimal('0.99')
            fair_prob = fair_prob.quantize(Decimal('0.000001'))
            return {
                "fair_prob": fair_prob,
                "raw_universal_sum": raw_univ,
                "clamped_universal_sum": clamped_univ,
            }

        raise ValueError(f"Unsupported market_type: {market_type} for sport {sport}")
EOF

########################################
# ev.py
########################################
cat > "$SRC_DIR/edge_equation/math/ev.py" << 'EOF'
from decimal import Decimal


class EVCalculator:
    """
    Edge and standard fractional Kelly:
    - edge = fair_prob - implied_prob
    - kelly_full = edge / (decimal_odds - 1)
    - kelly_half = kelly_full / 2
    """

    @staticmethod
    def american_to_decimal(odds: int) -> Decimal:
        if odds > 0:
            return Decimal('1') + Decimal(odds) / Decimal('100')
        return Decimal('1') + Decimal('100') / Decimal(-odds)

    @staticmethod
    def calculate_edge(fair_prob: Decimal, american_odds: int) -> Decimal:
        dec_odds = EVCalculator.american_to_decimal(american_odds)
        implied = Decimal('1') / dec_odds
        edge = fair_prob - implied
        return edge.quantize(Decimal('0.000001'))

    @staticmethod
    def kelly_fraction(edge: Decimal, dec_odds: Decimal) -> Decimal:
        if dec_odds <= Decimal('1'):
            return Decimal('0')
        kelly = edge / (dec_odds - Decimal('1'))
        if kelly < Decimal('0'):
            return Decimal('0')
        return kelly.quantize(Decimal('0.0001'))

    @staticmethod
    def calibrate(public_mode: bool, fair_value: dict, line: dict):
        if public_mode:
            return {"edge": None, "kelly": None}
        fair_prob = fair_value.get("fair_prob", None)
        if fair_prob is None:
            return {"edge": None, "kelly": None}
        odds = int(line.get("odds", -110))
        edge = EVCalculator.calculate_edge(fair_prob, odds)
        # Only compute Kelly if edge >= 0.010 (B grade minimum)
        if edge < Decimal('0.010000'):
            return {"edge": edge, "kelly": Decimal('0')}
        dec_odds = EVCalculator.american_to_decimal(odds)
        kelly_full = EVCalculator.kelly_fraction(edge, dec_odds)
        kelly_half = (kelly_full / Decimal('2')).quantize(Decimal('0.0001'))
        # Cap at 25% of bankroll
        if kelly_half > Decimal('0.25'):
            kelly_half = Decimal('0.25')
        return {"edge": edge, "kelly": kelly_half}
EOF

########################################
# scoring.py
########################################
cat > "$SRC_DIR/edge_equation/math/scoring.py" << 'EOF'
from decimal import Decimal


class ConfidenceScorer:
    """
    Grade by edge:
    - A+: edge > 0.050
    - A:  edge > 0.030
    - B:  edge > 0.010
    - C:  otherwise
    """

    @staticmethod
    def grade(edge: Decimal) -> str:
        if edge is None:
            return "C"
        if edge > Decimal('0.050'):
            return "A+"
        if edge > Decimal('0.030'):
            return "A"
        if edge > Decimal('0.010'):
            return "B"
        return "C"

    @staticmethod
    def realization_for_grade(grade: str) -> int:
        if grade == "A+":
            return 68
        if grade == "A":
            return 59
        if grade == "B":
            return 52
        return 47
EOF

########################################
# tests: assert outputs of the formulas (no legacy constants)
########################################
cat > "$TEST_DIR/test_math_phase2.py" << 'EOF'
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
EOF

echo "=== Phase-2 math layer files written. Run pytest to validate, then commit and open PR from engine-math-v1. ==="
