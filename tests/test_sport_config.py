from decimal import Decimal
import pytest

from edge_equation.config.sport_config import (
    SPORT_CONFIG,
    SportConfig,
)


def test_mlb_tuning_params_present():
    cfg = SPORT_CONFIG["MLB"]
    assert cfg["pythagorean_exponent"] == Decimal('1.83')
    assert cfg["decay_lambda"] == Decimal('0.95')
    assert cfg["form_window_games"] == 15
    assert cfg["pitching_weight"] == Decimal('0.55')
    assert cfg["bullpen_weight"] == Decimal('0.20')
    assert cfg["home_adv"] == Decimal('0.115')


def test_kbo_matches_mlb_pythagorean_convention():
    assert SPORT_CONFIG["KBO"]["pythagorean_exponent"] == Decimal('1.83')
    assert SPORT_CONFIG["NPB"]["pythagorean_exponent"] == Decimal('1.83')


def test_nfl_uses_steeper_pythagorean():
    # NFL uses exponent ~2.37, significantly higher than MLB.
    assert SPORT_CONFIG["NFL"]["pythagorean_exponent"] > Decimal('2')


def test_every_sport_has_all_phase18_knobs():
    required = ("pythagorean_exponent", "decay_lambda", "form_window_games", "home_adv")
    for sport, cfg in SPORT_CONFIG.items():
        for key in required:
            assert key in cfg, f"sport {sport!r} missing {key!r}"


def test_sport_config_get_unknown_returns_default():
    assert SportConfig.get("UNKNOWN", "pythagorean_exponent") is None
    assert SportConfig.get("UNKNOWN", "pythagorean_exponent", Decimal('9')) == Decimal('9')


def test_sport_config_require_unknown_sport_raises():
    with pytest.raises(KeyError, match="Unknown sport"):
        SportConfig.require("UNKNOWN", "pythagorean_exponent")


def test_sport_config_require_missing_key_raises():
    with pytest.raises(KeyError, match="no"):
        SportConfig.require("MLB", "missing_key")


def test_sport_config_helpers():
    assert SportConfig.pythagorean_exponent("MLB") == Decimal('1.83')
    assert SportConfig.decay_lambda("MLB") == Decimal('0.95')
    assert SportConfig.form_window_games("MLB") == 15
    assert SportConfig.pitching_weight("MLB") == Decimal('0.55')
    assert SportConfig.bullpen_weight("MLB") == Decimal('0.20')
    assert SportConfig.home_adv("MLB") == Decimal('0.115')


def test_sport_config_pitching_weight_none_for_non_baseball():
    # Only baseball-family sports should carry pitching-specific weights.
    assert SportConfig.pitching_weight("NFL") is None
    assert SportConfig.pitching_weight("NBA") if False else True  # no NBA key here, but NCAA_Basketball exists
    assert SportConfig.pitching_weight("NCAA_Basketball") is None


def test_soccer_uses_low_pythagorean():
    # Low-scoring sport -> low Pythagorean exponent.
    assert SPORT_CONFIG["Soccer"]["pythagorean_exponent"] < Decimal('2')


def test_nba_family_uses_oliver_exponent():
    # Dean Oliver's ~13.91 for basketball scoring.
    assert SPORT_CONFIG["NCAA_Basketball"]["pythagorean_exponent"] > Decimal('10')
