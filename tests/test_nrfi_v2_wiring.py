"""Tests for the NRFI v2 Phase 2 wiring.

Covers:

  * ``_arsenal_for_pitcher`` --- builds a populated ``PitchArsenal``
    from a Statcast frame, and falls back to the league-prior
    default when the frame is empty / pitcher_id is 0.
  * ``_first_inn_or_empty`` --- empty-frame guard.
  * ``_is_opener`` --- reads ``{side}_pitcher_role`` when present,
    defaults to False otherwise.
  * ``NRFI_DISABLE_F1_V2`` env flag --- short-circuits all three
    helpers so the operator can A/B retrain on the same backfill.
  * ``feature_diff.summarize_columns`` --- per-column distribution
    summary the operator runs pre-retrain to spot all-default
    columns (which would indicate a Statcast wiring bug).
"""

from __future__ import annotations

import pytest


pytest.importorskip("pandas")


def _statcast_frame_with_pitcher_1():
    """Six F1 pitches for pitcher_id=1 across two PAs --- enough to
    populate pitch-mix and F-strike distinct from the league default."""
    import pandas as pd
    rows = [
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 1,
         "description": "called_strike", "events": None, "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 2,
         "description": "ball", "events": None, "zone": 12,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "FF", "pitch_number": 3,
         "description": "swinging_strike", "events": "strikeout", "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "SI", "pitch_number": 1,
         "description": "ball", "events": None, "zone": 13,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "CU", "pitch_number": 2,
         "description": "called_strike", "events": None, "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
        {"pitcher": 1, "pitch_type": "CU", "pitch_number": 3,
         "description": "hit_into_play", "events": "single", "zone": 5,
         "home_plate_umpire_id": 100, "game_pk": 1, "post_bat_score": 0},
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# _arsenal_for_pitcher
# ---------------------------------------------------------------------------


def test_arsenal_for_pitcher_populates_f1_mix_from_statcast():
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _arsenal_for_pitcher,
    )
    df = _statcast_frame_with_pitcher_1()
    a = _arsenal_for_pitcher(df, pitcher_id=1)
    # 6 pitches: 3 FF + 1 SI + 2 CU
    assert a.f1_mix_ff_pct == pytest.approx(3 / 6, abs=1e-6)
    assert a.f1_mix_si_pct == pytest.approx(1 / 6, abs=1e-6)
    assert a.f1_mix_cu_pct == pytest.approx(2 / 6, abs=1e-6)
    assert a.f1_arsenal_depth == 3.0


def test_arsenal_for_pitcher_empty_frame_returns_default_arsenal():
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _arsenal_for_pitcher,
    )
    a = _arsenal_for_pitcher(None, pitcher_id=1)
    # Should match neutral PitchArsenal defaults --- 0.40 FF, etc.
    assert a.f1_mix_ff_pct == pytest.approx(0.40, abs=1e-6)
    assert a.f_strike_pct == pytest.approx(0.62, abs=1e-6)


def test_arsenal_for_pitcher_zero_pitcher_id_returns_default_arsenal():
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _arsenal_for_pitcher,
    )
    df = _statcast_frame_with_pitcher_1()
    a = _arsenal_for_pitcher(df, pitcher_id=0)
    assert a.f1_mix_ff_pct == pytest.approx(0.40, abs=1e-6)


def test_arsenal_for_pitcher_carries_seasonwide_defaults_through():
    """The seasonwide arsenal (velo / spin / csw) defaults still flow
    through --- F1 mix populates *additionally*, doesn't reset others."""
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _arsenal_for_pitcher,
    )
    df = _statcast_frame_with_pitcher_1()
    a = _arsenal_for_pitcher(df, pitcher_id=1)
    assert a.fb_velo_mph == 93.5    # default carries through
    assert a.csw_pct == 0.295        # default carries through


# ---------------------------------------------------------------------------
# _first_inn_or_empty
# ---------------------------------------------------------------------------


def test_first_inn_or_empty_returns_dict_for_known_pitcher():
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _first_inn_or_empty,
    )
    df = _statcast_frame_with_pitcher_1()
    out = _first_inn_or_empty(df, pitcher_id=1)
    assert "p1_inn_k_pct" in out
    assert out["p1_inn_pa"] >= 1


def test_first_inn_or_empty_handles_none_frame():
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _first_inn_or_empty,
    )
    assert _first_inn_or_empty(None, pitcher_id=1) == {}
    assert _first_inn_or_empty(None, pitcher_id=0) == {}


# ---------------------------------------------------------------------------
# _is_opener
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_is_opener_returns_false_when_role_field_missing():
    from edge_equation.engines.nrfi.evaluation.backtest import _is_opener
    g = _FakeRow()  # no role attribute
    assert _is_opener(g, "home") is False
    assert _is_opener(g, "away") is False


def test_is_opener_returns_true_when_role_is_opener():
    from edge_equation.engines.nrfi.evaluation.backtest import _is_opener
    g = _FakeRow(home_pitcher_role="opener", away_pitcher_role=None)
    assert _is_opener(g, "home") is True
    assert _is_opener(g, "away") is False


def test_is_opener_recognizes_bullpen_and_reliever_synonyms():
    from edge_equation.engines.nrfi.evaluation.backtest import _is_opener
    g = _FakeRow(home_pitcher_role="Bullpen", away_pitcher_role="reliever")
    assert _is_opener(g, "home") is True
    assert _is_opener(g, "away") is True


# ---------------------------------------------------------------------------
# NRFI_DISABLE_F1_V2 flag
# ---------------------------------------------------------------------------


def test_disable_v2_flag_short_circuits_arsenal(monkeypatch):
    from edge_equation.engines.nrfi.evaluation.backtest import (
        _arsenal_for_pitcher,
    )
    monkeypatch.setenv("NRFI_DISABLE_F1_V2", "1")
    df = _statcast_frame_with_pitcher_1()
    a = _arsenal_for_pitcher(df, pitcher_id=1)
    # With the flag set the arsenal returns to defaults regardless of
    # the populated frame.
    assert a.f1_mix_ff_pct == pytest.approx(0.40, abs=1e-6)


def test_disable_v2_flag_short_circuits_opener(monkeypatch):
    from edge_equation.engines.nrfi.evaluation.backtest import _is_opener
    monkeypatch.setenv("NRFI_DISABLE_F1_V2", "1")
    g = _FakeRow(home_pitcher_role="opener")
    assert _is_opener(g, "home") is False


# ---------------------------------------------------------------------------
# feature_diff harness
# ---------------------------------------------------------------------------


def test_summarize_columns_flags_missing_columns():
    from edge_equation.engines.nrfi.evaluation.feature_diff import (
        summarize_columns,
    )
    rows = [{"some_other_col": 1.0}, {"some_other_col": 2.0}]
    report = summarize_columns(rows, columns=("home_p_f1_mix_ff",))
    assert "home_p_f1_mix_ff" in report.columns_missing
    assert report.columns_present == []


def test_summarize_columns_distribution_stats():
    from edge_equation.engines.nrfi.evaluation.feature_diff import (
        summarize_columns,
    )
    # Three rows with varying f1_mix_ff values, away cols all default.
    rows = [
        {"home_p_f1_mix_ff": 0.30, "away_p_f1_mix_ff": 0.40},
        {"home_p_f1_mix_ff": 0.50, "away_p_f1_mix_ff": 0.40},
        {"home_p_f1_mix_ff": 0.45, "away_p_f1_mix_ff": 0.40},
    ]
    report = summarize_columns(
        rows,
        columns=("home_p_f1_mix_ff", "away_p_f1_mix_ff"),
    )
    by_col = {s.column: s for s in report.summaries}
    home = by_col["home_p_f1_mix_ff"]
    away = by_col["away_p_f1_mix_ff"]
    assert home.n == 3
    assert away.n == 3
    # Away column is 100% at the default value (0.40).
    assert away.fraction_default() == pytest.approx(1.0, abs=1e-6)
    # Home column should have non-trivial spread.
    assert home.std > 0
    assert home.fraction_default() < 0.5


def test_render_report_flags_suspicious_columns_in_text():
    from edge_equation.engines.nrfi.evaluation.feature_diff import (
        render_report, summarize_columns,
    )
    rows = [{"home_p_f1_mix_ff": 0.40} for _ in range(20)]
    report = summarize_columns(rows, columns=("home_p_f1_mix_ff",))
    text = render_report(report)
    assert "WARNING" in text
    assert "95%" in text


def test_v2_features_disabled_reads_env(monkeypatch):
    from edge_equation.engines.nrfi.evaluation.feature_diff import (
        v2_features_disabled,
    )
    monkeypatch.delenv("NRFI_DISABLE_F1_V2", raising=False)
    assert v2_features_disabled() is False
    monkeypatch.setenv("NRFI_DISABLE_F1_V2", "true")
    assert v2_features_disabled() is True
    monkeypatch.setenv("NRFI_DISABLE_F1_V2", "0")
    assert v2_features_disabled() is False
