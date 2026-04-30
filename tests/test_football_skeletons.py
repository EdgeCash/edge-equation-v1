"""Phase F-1 smoke test — football engine skeletons.

Verifies the package shape we're committing to so future phases
have a stable surface to build against. No behavior tested here
beyond imports + obvious structural invariants — the modules
themselves are stubs.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# football_core
# ---------------------------------------------------------------------------


def test_football_core_imports_cleanly():
    from edge_equation.engines.football_core import (
        FootballMarket,
        PROP_MARKET_LABELS,
        QBAdjustment,
        QBStatus,
        RestProfile,
        SHARED_FOOTBALL_MARKETS,
        VenueWeatherProfile,
        classify_rest,
        expected_points_delta_for,
        is_outdoor,
        weather_impact_score,
    )
    assert FootballMarket is not None
    assert "Spread" in SHARED_FOOTBALL_MARKETS
    assert "Total" in SHARED_FOOTBALL_MARKETS
    assert "ML" in SHARED_FOOTBALL_MARKETS


def test_football_core_prop_labels_cover_basics():
    from edge_equation.engines.football_core import PROP_MARKET_LABELS
    for canonical in (
        "Pass_Yds", "Rush_Yds", "Rec_Yds", "Anytime_TD",
    ):
        assert canonical in PROP_MARKET_LABELS


def test_football_core_qb_status_enum_complete():
    from edge_equation.engines.football_core import QBStatus
    expected = {"HEALTHY", "PROBABLE", "QUESTIONABLE", "DOUBTFUL",
                  "OUT", "IR"}
    assert {s.value for s in QBStatus} == expected


def test_football_core_qb_adjustment_delta_signs():
    from edge_equation.engines.football_core import (
        QBStatus, expected_points_delta_for,
    )
    healthy = expected_points_delta_for(QBStatus.HEALTHY)
    out = expected_points_delta_for(QBStatus.OUT)
    assert healthy.delta == 0.0
    assert out.delta < 0.0


def test_football_core_rest_classification():
    from datetime import date
    from edge_equation.engines.football_core import classify_rest
    # Thu game after a Sun game = 4-day rest = short.
    short = classify_rest(
        "NYG", last_game_date=date(2026, 9, 14),
        this_game_date=date(2026, 9, 18),
    )
    assert short.bucket == "short"
    # Bye-week override.
    bye = classify_rest(
        "NYG", last_game_date=date(2026, 9, 7),
        this_game_date=date(2026, 9, 21),
        is_bye_week=True,
    )
    assert bye.bucket == "bye"


def test_football_core_weather_impact_bounded():
    from edge_equation.engines.football_core import weather_impact_score
    # Calm / mild → 0.0
    assert weather_impact_score(
        wind_mph=5.0, temperature_f=60.0, precipitation_prob=0.0,
    ) == 0.0
    # Severe weather → bounded at -0.10
    severe = weather_impact_score(
        wind_mph=30.0, temperature_f=15.0, precipitation_prob=0.9,
    )
    assert severe >= -0.10


def test_football_core_dome_overrides_outdoor():
    from edge_equation.engines.football_core import (
        VenueWeatherProfile, is_outdoor,
    )
    dome = VenueWeatherProfile(
        venue_code="ATL", venue_name="Mercedes-Benz Stadium",
        is_dome=True, is_retractable=False,
    )
    assert is_outdoor(dome) is False
    open_air = VenueWeatherProfile(
        venue_code="GB", venue_name="Lambeau Field",
        is_dome=False, is_retractable=False,
    )
    assert is_outdoor(open_air) is True


# ---------------------------------------------------------------------------
# NFL skeleton
# ---------------------------------------------------------------------------


def test_nfl_imports_cleanly():
    from edge_equation.engines.nfl import (
        NFLConfig, ProjectionKnobs, get_default_config, NFL_MARKETS,
    )
    assert NFL_MARKETS["Spread"].canonical == "Spread"


def test_nfl_config_resolves_paths():
    from edge_equation.engines.nfl import NFLConfig
    cfg = NFLConfig().resolve_paths()
    assert cfg.cache_dir.exists()
    assert cfg.duckdb_path.parent.exists()


def test_nfl_projection_knobs_have_football_sized_priors():
    """NFL sample sizes are tighter than MLB → heavier prior weight."""
    from edge_equation.engines.nfl import ProjectionKnobs
    knobs = ProjectionKnobs()
    assert knobs.lookback_games <= 8
    assert knobs.prior_weight_games >= 4.0


def test_nfl_subpackages_importable():
    """Each sub-package is a valid Python package, even if empty."""
    import importlib
    for name in ("features", "models", "calibration", "output", "source"):
        mod = importlib.import_module(f"edge_equation.engines.nfl.{name}")
        assert mod is not None


def test_nfl_daily_stub_returns_empty_card():
    from edge_equation.engines.nfl.daily import build_nfl_card
    card = build_nfl_card("2026-09-14")
    assert card.target_date == "2026-09-14"
    assert card.picks == []
    assert "skeleton" in card.notes.lower()


def test_nfl_output_payload_has_football_audit_columns():
    """NFL-specific audit columns (rest, QB, weather) live on the payload."""
    from edge_equation.engines.nfl.output import NFLOutput
    out = NFLOutput()
    assert hasattr(out, "rest_bucket_home")
    assert hasattr(out, "qb_status_home")
    assert hasattr(out, "weather_impact")


def test_nfl_ledger_ddl_has_week_column():
    """NFL ledger PK includes a week column (NFL is week-based)."""
    from edge_equation.engines.nfl.ledger import _DDL_PICK_SETTLED
    assert "week" in _DDL_PICK_SETTLED


# ---------------------------------------------------------------------------
# NCAAF skeleton
# ---------------------------------------------------------------------------


def test_ncaaf_imports_cleanly():
    from edge_equation.engines.ncaaf import (
        NCAAFConfig, ProjectionKnobs, get_default_config, NCAAF_MARKETS,
    )
    assert NCAAF_MARKETS["Spread"].canonical == "Spread"


def test_ncaaf_config_resolves_paths():
    from edge_equation.engines.ncaaf import NCAAFConfig
    cfg = NCAAFConfig().resolve_paths()
    assert cfg.cache_dir.exists()
    assert cfg.duckdb_path.parent.exists()


def test_ncaaf_projection_knobs_heavier_than_nfl():
    """NCAAF has wider variance → heavier prior than NFL."""
    from edge_equation.engines.nfl import ProjectionKnobs as NFLKnobs
    from edge_equation.engines.ncaaf import ProjectionKnobs as NCAAFKnobs
    nfl_knobs = NFLKnobs()
    ncaaf_knobs = NCAAFKnobs()
    assert ncaaf_knobs.prior_weight_games >= nfl_knobs.prior_weight_games


def test_ncaaf_uses_conference_tier_prior_flag():
    from edge_equation.engines.ncaaf import ProjectionKnobs
    knobs = ProjectionKnobs()
    assert knobs.use_conference_tier_prior is True


def test_ncaaf_subpackages_importable():
    import importlib
    for name in ("features", "models", "calibration", "output", "source"):
        mod = importlib.import_module(
            f"edge_equation.engines.ncaaf.{name}",
        )
        assert mod is not None


def test_ncaaf_daily_stub_returns_empty_card():
    from edge_equation.engines.ncaaf.daily import build_ncaaf_card
    card = build_ncaaf_card("2026-09-12")
    assert card.target_date == "2026-09-12"
    assert card.picks == []


def test_ncaaf_output_payload_has_conference_columns():
    """NCAAF payload carries conference + recruit-rating audit fields."""
    from edge_equation.engines.ncaaf.output import NCAAFOutput
    out = NCAAFOutput()
    assert hasattr(out, "conference_home")
    assert hasattr(out, "conference_tier_home")
    assert hasattr(out, "recruit_rating_delta")


# ---------------------------------------------------------------------------
# Run-daily entry stubs
# ---------------------------------------------------------------------------


def test_run_daily_nfl_module_importable():
    import edge_equation.run_daily_nfl as mod
    assert callable(mod.main)


def test_run_daily_ncaaf_module_importable():
    import edge_equation.run_daily_ncaaf as mod
    assert callable(mod.main)


def test_run_daily_nfl_main_delegates_to_nfl_daily(monkeypatch):
    captured = []
    def _fake(argv=None):
        captured.append(argv)
        return 0
    monkeypatch.setattr(
        "edge_equation.engines.nfl.daily.main", _fake,
    )
    from edge_equation.run_daily_nfl import main
    rc = main(["--date", "2026-09-14"])
    assert rc == 0
    assert captured == [["--date", "2026-09-14"]]


def test_run_daily_ncaaf_main_delegates_to_ncaaf_daily(monkeypatch):
    captured = []
    def _fake(argv=None):
        captured.append(argv)
        return 0
    monkeypatch.setattr(
        "edge_equation.engines.ncaaf.daily.main", _fake,
    )
    from edge_equation.run_daily_ncaaf import main
    rc = main(["--date", "2026-09-13"])
    assert rc == 0
    assert captured == [["--date", "2026-09-13"]]


# ---------------------------------------------------------------------------
# MLB hands-off invariant — make sure we didn't touch it
# ---------------------------------------------------------------------------


def test_mlb_engines_still_importable():
    """Hard rule: this PR does not modify any MLB engine. Verify the
    NRFI / Props / Full-Game packages still import cleanly."""
    import edge_equation.engines.nrfi  # noqa: F401
    import edge_equation.engines.props_prizepicks  # noqa: F401
    import edge_equation.engines.full_game  # noqa: F401
