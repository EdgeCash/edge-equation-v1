"""Tests for the full-game per-tier YTD ledger.

Mirrors `test_props_ledger.py` and `test_nrfi_ledger.py`. Uses a
fake DuckDB-like store responding to the SQL strings the ledger emits,
so the suite stays runnable without duckdb installed.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from edge_equation.engines.full_game import ledger as ledger_mod
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Pure helpers — payout + hit detection
# ---------------------------------------------------------------------------


def test_pick_payout_units_minus_110():
    payout = ledger_mod._pick_payout_units(-110.0)
    assert payout == pytest.approx(100.0 / 110.0)


def test_pick_payout_units_plus_money():
    assert ledger_mod._pick_payout_units(+135.0) == pytest.approx(1.35)


# ---- Total / F5_Total / Team_Total ----------------------------------------


def test_total_over_hits_when_actual_exceeds_line():
    assert ledger_mod._did_side_hit(
        "Total", "Over", "", "NYY", 8.5,
        actual_home=5, actual_away=4,
    ) is True


def test_total_over_misses_when_actual_under_line():
    assert ledger_mod._did_side_hit(
        "Total", "Over", "", "NYY", 8.5,
        actual_home=3, actual_away=4,
    ) is False


def test_total_under_complementary():
    assert ledger_mod._did_side_hit(
        "Total", "Under", "", "NYY", 8.5,
        actual_home=3, actual_away=4,
    ) is True


def test_team_total_uses_home_runs_when_team_is_home():
    assert ledger_mod._did_side_hit(
        "Team_Total", "Over", "NYY", "NYY", 4.5,
        actual_home=5, actual_away=2,
    ) is True


def test_team_total_uses_away_runs_when_team_is_away():
    assert ledger_mod._did_side_hit(
        "Team_Total", "Over", "BOS", "NYY", 4.5,
        actual_home=2, actual_away=5,
    ) is True


def test_f5_total_uses_f5_runs_not_full_game():
    """The settle layer uses F5 columns for F5 markets — full-game
    blowouts shouldn't make a low-scoring F5 settle as Over."""
    # Full-game total = 20 but F5 total = 2+1 = 3 → Under 4.5 hits.
    assert ledger_mod._did_side_hit(
        "F5_Total", "Over", "", "NYY", 4.5,
        actual_home=10, actual_away=10,
        f5_home=2, f5_away=1,
    ) is False
    assert ledger_mod._did_side_hit(
        "F5_Total", "Under", "", "NYY", 4.5,
        actual_home=10, actual_away=10,
        f5_home=2, f5_away=1,
    ) is True


def test_f5_total_missing_f5_columns_returns_false():
    assert ledger_mod._did_side_hit(
        "F5_Total", "Over", "", "NYY", 4.5,
        actual_home=5, actual_away=4,
        f5_home=None, f5_away=None,
    ) is False


# ---- ML / F5_ML -----------------------------------------------------------


def test_ml_home_pick_wins_when_home_outscores():
    assert ledger_mod._did_side_hit(
        "ML", "NYY", "NYY", "NYY", None,
        actual_home=5, actual_away=4,
    ) is True


def test_ml_home_pick_loses_when_away_outscores():
    assert ledger_mod._did_side_hit(
        "ML", "NYY", "NYY", "NYY", None,
        actual_home=3, actual_away=4,
    ) is False


def test_ml_away_pick_wins_when_away_outscores():
    assert ledger_mod._did_side_hit(
        "ML", "BOS", "BOS", "NYY", None,
        actual_home=3, actual_away=4,
    ) is True


def test_f5_ml_uses_f5_columns():
    assert ledger_mod._did_side_hit(
        "F5_ML", "BOS", "BOS", "NYY", None,
        actual_home=10, actual_away=2,         # full-game NYY blowout
        f5_home=2, f5_away=3,                  # but BOS led after 5
    ) is True


# ---- Run_Line -------------------------------------------------------------


def test_run_line_minus_15_covers_when_win_by_2plus():
    """NYY -1.5 needs margin > 1.5 → win by 2 runs or more."""
    assert ledger_mod._did_side_hit(
        "Run_Line", "NYY", "NYY", "NYY", -1.5,
        actual_home=5, actual_away=2,
    ) is True


def test_run_line_minus_15_misses_on_one_run_win():
    assert ledger_mod._did_side_hit(
        "Run_Line", "NYY", "NYY", "NYY", -1.5,
        actual_home=5, actual_away=4,
    ) is False


def test_run_line_plus_15_covers_when_lose_by_one():
    """BOS +1.5 covers when BOS loses by 1 (or wins outright)."""
    assert ledger_mod._did_side_hit(
        "Run_Line", "BOS", "BOS", "NYY", +1.5,
        actual_home=5, actual_away=4,
    ) is True


def test_run_line_plus_15_misses_when_blown_out():
    """BOS +1.5 fails when BOS loses by 2+ runs."""
    assert ledger_mod._did_side_hit(
        "Run_Line", "BOS", "BOS", "NYY", +1.5,
        actual_home=8, actual_away=2,
    ) is False


def test_unknown_market_returns_false():
    assert ledger_mod._did_side_hit(
        "MysteryMarket", "side", "", "", None,
        actual_home=5, actual_away=4,
    ) is False


# ---------------------------------------------------------------------------
# SettlementResult formatting
# ---------------------------------------------------------------------------


def test_settlement_result_init_zeros_by_tier():
    r = ledger_mod.SettlementResult()
    for t in Tier:
        assert r.by_tier[t] == 0


def test_settlement_result_summary_renders_tier_breakdown():
    r = ledger_mod.SettlementResult(
        n_picks_examined=10, n_picks_already_settled=3,
        n_picks_settled=5, n_picks_no_actual=2,
    )
    r.by_tier[Tier.ELITE] = 1
    r.by_tier[Tier.STRONG] = 4
    text = r.summary()
    assert "Full-game settlement run" in text
    assert "newly settled          5" in text
    assert "ELITE" in text
    assert "STRONG" in text


# ---------------------------------------------------------------------------
# Fake store + DDL idempotency
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.upserts: list[tuple[str, list[dict]]] = []
        self._query_responses: list[tuple[str, pd.DataFrame]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql.strip(), tuple(params or ())))

    def upsert(self, table: str, rows) -> int:
        rows = list(rows)
        self.upserts.append((table, rows))
        return len(rows)

    def queue_query(self, needle: str, df: pd.DataFrame) -> None:
        self._query_responses.append((needle, df))

    def query_df(self, sql: str, params: tuple = ()):
        normalised = " ".join(sql.split())
        for i, (needle, df) in enumerate(self._query_responses):
            if needle in normalised:
                self._query_responses.pop(i)
                return df
        raise AssertionError(
            f"unexpected query_df: no canned response for SQL\n  {normalised!r}"
        )


def test_init_ledger_tables_creates_both_tables():
    store = _FakeStore()
    ledger_mod.init_ledger_tables(store)
    blob = " ".join(s for s, _ in store.executed)
    assert "fullgame_pick_settled" in blob
    assert "fullgame_tier_ledger" in blob


# ---------------------------------------------------------------------------
# settle_predictions
# ---------------------------------------------------------------------------


def _pred_df(rows):
    return pd.DataFrame(rows, columns=[
        "game_pk", "market_type", "side", "team_tricode",
        "line_value", "tier", "american_odds",
        "predicted_p", "event_date",
        "home_runs", "away_runs", "f5_home_runs", "f5_away_runs",
    ])


def _empty_settled_df():
    return pd.DataFrame(columns=["game_pk", "market_type",
                                       "side", "line_value"])


def test_settle_predictions_classifies_total_over_lock_win():
    store = _FakeStore()
    preds = _pred_df([
        # LOCK Over 8.5 win — total = 12 actual.
        (700001, "Total", "Over", "", 8.5, "ELITE",
           -110.0, 0.62, "2026-04-15", 7, 5, 4, 3),
    ])
    store.queue_query("FROM fullgame_predictions p JOIN fullgame_actuals", preds)
    store.queue_query("FROM fullgame_pick_settled", _empty_settled_df())
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_settled == 1
    assert result.by_tier[Tier.ELITE] == 1
    rows = [u for u in store.upserts if u[0] == "fullgame_pick_settled"][0][1]
    assert rows[0]["actual_hit"] is True
    assert rows[0]["units_delta"] == pytest.approx(100.0 / 110.0)


def test_settle_predictions_classifies_ml_loss_at_negative_odds():
    store = _FakeStore()
    preds = _pred_df([
        # STRONG NYY ML, NYY loses 3-5.
        (700002, "ML", "NYY", "NYY", 0.0, "STRONG",
           -150.0, 0.65, "2026-04-15", 3, 5, 2, 2),
    ])
    store.queue_query("FROM fullgame_predictions p JOIN fullgame_actuals", preds)
    store.queue_query("FROM fullgame_pick_settled", _empty_settled_df())
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    rows = [u for u in store.upserts if u[0] == "fullgame_pick_settled"][0][1]
    assert rows[0]["actual_hit"] is False
    assert rows[0]["units_delta"] == pytest.approx(-1.0)


def test_settle_predictions_idempotent():
    store = _FakeStore()
    preds = _pred_df([
        (700001, "Total", "Over", "", 8.5, "ELITE",
           -110.0, 0.62, "2026-04-15", 7, 5, 4, 3),
    ])
    settled = pd.DataFrame([
        {"game_pk": 700001, "market_type": "Total",
           "side": "Over", "line_value": 8.5},
    ])
    store.queue_query("FROM fullgame_predictions p JOIN fullgame_actuals", preds)
    store.queue_query("FROM fullgame_pick_settled", settled)
    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_already_settled == 1
    assert result.n_picks_settled == 0


def test_settle_predictions_skips_nan_actuals():
    store = _FakeStore()
    preds = _pred_df([
        (700001, "Total", "Over", "", 8.5, "ELITE",
           -110.0, 0.62, "2026-04-15", math.nan, math.nan, None, None),
    ])
    store.queue_query("FROM fullgame_predictions p JOIN fullgame_actuals", preds)
    store.queue_query("FROM fullgame_pick_settled", _empty_settled_df())
    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_settled == 0


def test_settle_predictions_empty_returns_zero():
    store = _FakeStore()
    store.queue_query("FROM fullgame_predictions p JOIN fullgame_actuals",
                        _pred_df([]))
    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_examined == 0


# ---------------------------------------------------------------------------
# Refresh + read API
# ---------------------------------------------------------------------------


def test_refresh_tier_ledger_writes_per_tier_market_and_rollups():
    store = _FakeStore()
    per_tier = pd.DataFrame([
        {"season": 2026, "market_type": "Total", "tier": "ELITE",
           "n_settled": 4, "wins": 3, "losses": 1, "units_won": 1.5},
        {"season": 2026, "market_type": "ML", "tier": "STRONG",
           "n_settled": 6, "wins": 4, "losses": 2, "units_won": 1.0},
    ])
    per_market = pd.DataFrame([
        {"season": 2026, "market_type": "Total",
           "n_settled": 4, "wins": 3, "losses": 1, "units_won": 1.5},
        {"season": 2026, "market_type": "ML",
           "n_settled": 6, "wins": 4, "losses": 2, "units_won": 1.0},
    ])
    total = pd.DataFrame([
        {"season": 2026, "n_settled": 10, "wins": 7, "losses": 3,
           "units_won": 2.5},
    ])
    store.queue_query("GROUP BY season, market_type, tier", per_tier)
    store.queue_query("GROUP BY season, market_type", per_market)
    store.queue_query("GROUP BY season", total)
    ledger_mod._refresh_tier_ledger(store, season=2026)
    delete_calls = [s for s, _ in store.executed if s.startswith("DELETE")]
    assert any("fullgame_tier_ledger" in s for s in delete_calls)
    writes = [u for u in store.upserts if u[0] == "fullgame_tier_ledger"]
    assert len(writes) == 1
    rows = writes[0][1]
    assert len(rows) == 5  # 2 per-tier + 2 market rollups + 1 total
    rollup_keys = {(r["market_type"], r["tier"]) for r in rows}
    assert ("Total", "ALL") in rollup_keys
    assert ("ML", "ALL") in rollup_keys
    assert ("ALL", "ALL") in rollup_keys


def _ledger_df(rows):
    return pd.DataFrame(rows, columns=[
        "season", "market_type", "tier",
        "n_settled", "wins", "losses", "units_won", "last_updated",
    ])


def test_get_tier_ledger_orders_by_market_then_tier():
    store = _FakeStore()
    rows = _ledger_df([
        (2026, "ML",     "STRONG", 6, 4, 2, 1.0, None),
        (2026, "Total",  "ELITE",   4, 3, 1, 1.5, None),
        (2026, "ALL",    "ALL",    10, 7, 3, 2.5, None),
    ])
    store.queue_query("FROM fullgame_tier_ledger", rows)
    df = ledger_mod.get_tier_ledger(store, 2026)
    markets = list(df["market_type"])
    # ML rank=0 before Total rank=2; ALL last.
    assert markets[0] == "ML"
    assert markets[1] == "Total"
    assert markets[-1] == "ALL"


def test_render_ledger_section_empty_returns_empty_string():
    store = _FakeStore()
    store.queue_query("FROM fullgame_tier_ledger", pd.DataFrame())
    assert ledger_mod.render_ledger_section(store, 2026) == ""


def test_render_ledger_section_formats_record_units_roi():
    store = _FakeStore()
    rows = _ledger_df([
        (2026, "Total",   "ELITE",   4, 4, 0, 3.50, None),
        (2026, "Run_Line", "STRONG", 6, 4, 2, 0.40, None),
    ])
    store.queue_query("FROM fullgame_tier_ledger", rows)
    text = ledger_mod.render_ledger_section(store, 2026)
    assert "FULL-GAME YTD LEDGER (2026)" in text
    assert "Total" in text
    assert "Run_Line" in text
    assert "4-0" in text
    assert "+3.50u" in text
