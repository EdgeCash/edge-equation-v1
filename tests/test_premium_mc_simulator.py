import pytest
from decimal import Decimal

from edge_equation.premium.mc_simulator import MonteCarloSimulator


def test_simulate_binary_is_deterministic_with_fixed_seed():
    sim1 = MonteCarloSimulator(seed=42, iterations=1000)
    sim2 = MonteCarloSimulator(seed=42, iterations=1000)
    r1 = sim1.simulate_binary(Decimal("0.6"))
    r2 = sim2.simulate_binary(Decimal("0.6"))
    assert r1 == r2


def test_simulate_binary_returns_expected_keys():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_binary(Decimal("0.6"))
    assert set(r.keys()) == {"p10", "p50", "p90", "mean"}
    for k, v in r.items():
        assert isinstance(v, Decimal)


def test_simulate_binary_mean_near_input_prob():
    sim = MonteCarloSimulator(seed=42, iterations=5000)
    r = sim.simulate_binary(Decimal("0.6"))
    # Mean should be within 5 percentage points of the true probability
    assert abs(float(r["mean"]) - 0.6) < 0.05


def test_simulate_binary_quantile_ordering():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_binary(Decimal("0.6"))
    assert r["p10"] <= r["p50"] <= r["p90"]


def test_simulate_binary_out_of_range_raises():
    sim = MonteCarloSimulator()
    with pytest.raises(ValueError):
        sim.simulate_binary(Decimal("-0.1"))
    with pytest.raises(ValueError):
        sim.simulate_binary(Decimal("1.5"))


def test_simulate_total_is_deterministic_with_fixed_seed():
    sim1 = MonteCarloSimulator(seed=42, iterations=1000)
    sim2 = MonteCarloSimulator(seed=42, iterations=1000)
    r1 = sim1.simulate_total(Decimal("10.0"), Decimal("1.5"))
    r2 = sim2.simulate_total(Decimal("10.0"), Decimal("1.5"))
    assert r1 == r2


def test_simulate_total_returns_expected_keys():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_total(Decimal("10.0"), Decimal("1.5"))
    assert set(r.keys()) == {"p10", "p50", "p90", "mean"}
    for v in r.values():
        assert isinstance(v, Decimal)


def test_simulate_total_mean_near_input():
    sim = MonteCarloSimulator(seed=42, iterations=5000)
    r = sim.simulate_total(Decimal("10.0"), Decimal("1.5"))
    # Mean should be close to the input mean
    assert abs(float(r["mean"]) - 10.0) < 0.2


def test_simulate_total_quantile_ordering():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_total(Decimal("10.0"), Decimal("1.5"))
    assert r["p10"] <= r["p50"] <= r["p90"]


def test_simulate_total_negative_stdev_raises():
    sim = MonteCarloSimulator()
    with pytest.raises(ValueError):
        sim.simulate_total(Decimal("10.0"), Decimal("-1.0"))


def test_simulator_invalid_iterations_raises():
    with pytest.raises(ValueError):
        MonteCarloSimulator(iterations=0)
    with pytest.raises(ValueError):
        MonteCarloSimulator(iterations=-5)


def test_different_seeds_produce_different_outputs():
    sim1 = MonteCarloSimulator(seed=42, iterations=1000)
    sim2 = MonteCarloSimulator(seed=43, iterations=1000)
    r1 = sim1.simulate_binary(Decimal("0.6"))
    r2 = sim2.simulate_binary(Decimal("0.6"))
    # Means for 1000 samples should differ at 6-decimal precision with different seeds
    assert r1["mean"] != r2["mean"] or r1["p10"] != r2["p10"]
