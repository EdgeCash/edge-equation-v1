import pytest
from decimal import Decimal

from edge_equation.math.hfa import (
    HFA,
    HFACalculator,
    HFA_BASELINE,
    HFA_TEAM_OVERRIDE,
    HFA_VENUE_BONUS,
)


def test_baseline_values():
    assert HFA_BASELINE["NBA"] == Decimal('2.50')
    assert HFA_BASELINE["NFL"] == Decimal('1.80')
    assert HFA_BASELINE["NHL"] == Decimal('0.15')
    assert HFA_BASELINE["MLB"] == Decimal('0.08')
    assert HFA_BASELINE["SOCCER"] == Decimal('0.27')


def test_team_override_values():
    assert HFA_TEAM_OVERRIDE[("NBA", "DEN")] == Decimal('1.00')
    assert HFA_TEAM_OVERRIDE[("NBA", "UTA")] == Decimal('0.50')
    assert HFA_TEAM_OVERRIDE[("NFL", "DEN")] == Decimal('0.50')
    assert HFA_TEAM_OVERRIDE[("MLB", "COL")] == Decimal('0.40')


def test_venue_bonus_values():
    assert HFA_VENUE_BONUS[("NFL", "DOME")] == Decimal('0.50')


def test_baseline_only_no_team_no_venue():
    h = HFACalculator.get_home_adv("NBA")
    assert isinstance(h, HFA)
    assert h.baseline == Decimal('2.50')
    assert h.team_override is None
    assert h.venue_bonus == Decimal('0.00')
    assert h.total == Decimal('2.500000')


def test_baseline_with_unknown_team():
    h = HFACalculator.get_home_adv("NBA", team="LAL")
    assert h.team_override is None
    assert h.total == Decimal('2.500000')


def test_team_override_replaces_baseline_nba_den():
    h = HFACalculator.get_home_adv("NBA", team="DEN")
    assert h.baseline == Decimal('2.50')
    assert h.team_override == Decimal('1.00')
    assert h.total == Decimal('1.000000')  # override REPLACES baseline


def test_team_override_nfl_den():
    h = HFACalculator.get_home_adv("NFL", team="DEN")
    assert h.team_override == Decimal('0.50')
    assert h.total == Decimal('0.500000')


def test_venue_bonus_stacks_on_baseline():
    h = HFACalculator.get_home_adv("NFL", team="SEA", context={"venue": "DOME"})
    # SEA has no override -> use baseline 1.80 + 0.50 dome
    assert h.baseline == Decimal('1.80')
    assert h.team_override is None
    assert h.venue_bonus == Decimal('0.50')
    assert h.total == Decimal('2.300000')


def test_venue_bonus_stacks_on_team_override():
    h = HFACalculator.get_home_adv("NFL", team="DEN", context={"venue": "DOME"})
    # DEN override 0.50 + dome bonus 0.50 = 1.00
    assert h.team_override == Decimal('0.50')
    assert h.venue_bonus == Decimal('0.50')
    assert h.total == Decimal('1.000000')


def test_context_without_venue_is_noop():
    h = HFACalculator.get_home_adv("NBA", team="DEN", context={"weather": "clear"})
    assert h.venue_bonus == Decimal('0.00')


def test_mlb_col_override():
    h = HFACalculator.get_home_adv("MLB", team="COL")
    assert h.total == Decimal('0.400000')


def test_soccer_baseline():
    h = HFACalculator.get_home_adv("SOCCER")
    assert h.total == Decimal('0.270000')


def test_unknown_sport_raises():
    with pytest.raises(ValueError, match="Unknown sport"):
        HFACalculator.get_home_adv("CRICKET")


def test_hfa_frozen():
    h = HFACalculator.get_home_adv("NBA")
    with pytest.raises(Exception):
        h.total = Decimal('999')


def test_to_dict_has_string_values():
    h = HFACalculator.get_home_adv("NFL", team="DEN", context={"venue": "DOME"})
    d = h.to_dict()
    assert d["sport"] == "NFL"
    assert d["team"] == "DEN"
    assert d["baseline"] == "1.80"
    assert d["team_override"] == "0.50"
    assert d["venue_bonus"] == "0.50"
    assert d["total"] == "1.000000"


def test_to_dict_with_null_override():
    h = HFACalculator.get_home_adv("NBA")
    d = h.to_dict()
    assert d["team"] is None
    assert d["team_override"] is None
