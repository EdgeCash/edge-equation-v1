"""Tests for the props per-tier YTD ledger.

Mirrors `test_nrfi_ledger.py` shape — uses a fake DuckDB-like store
that responds to the SQL strings the ledger module emits, so the
suite stays runnable without duckdb installed locally.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from edge_equation.engines.props_prizepicks import ledger as ledger_mod
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_pick_payout_units_minus_115():
    payout = ledger_mod._pick_payout_units(-115.0)
    assert payout == pytest.approx(100.0 / 115.0)


def test_pick_payout_units_plus_money():
    assert ledger_mod._pick_payout_units(+250.0) == pytest.approx(2.50)


def test_did_side_hit_over_strict_inequality():
    assert ledger_mod._did_side_hit("Over", actual_value=2.0,
                                     line_value=1.5) is True
    assert ledger_mod._did_side_hit("Over", actual_value=1.0,
                                     line_value=1.5) is False


def test_did_side_hit_under_strict_inequality():
    assert ledger_mod._did_side_hit("Under", actual_value=1.0,
                                     line_value=1.5) is True
    assert ledger_mod._did_side_hit("Under", actual_value=2.0,
                                     line_value=1.5) is False


def test_did_side_hit_yes_alias_works_like_over():
    assert ledger_mod._did_side_hit("Yes", actual_value=1.0,
                                     line_value=0.5) is True
    assert ledger_mod._did_side_hit("No", actual_value=0.0,
                                     line_value=0.5) is True


def test_did_side_hit_unknown_side_returns_false():
    assert ledger_mod._did_side_hit("Push", actual_value=2.0,
                                     line_value=1.5) is False


# ---------------------------------------------------------------------------
# SettlementResult formatting
# ---------------------------------------------------------------------------


def test_settlement_result_init_zeros_by_tier():
    r = ledger_mod.SettlementResult()
    for t in Tier:
        assert r.by_tier[t] == 0


def test_settlement_result_summary_includes_tier_breakdown():
    r = ledger_mod.SettlementResult(
        n_picks_examined=8, n_picks_already_settled=2,
        n_picks_settled=4, n_picks_no_actual=2,
    )
    r.by_tier[Tier.LOCK] = 1
    r.by_tier[Tier.STRONG] = 3
    text = r.summary()
    assert "Props settlement run" in text
    assert "newly settled          4" in text
    assert "LOCK" in text
    assert "STRONG" in text
    assert "MODERATE" not in text  # zero-count tiers omitted


# ---------------------------------------------------------------------------
# Fake store
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


# ---------------------------------------------------------------------------
# init_ledger_tables
# ---------------------------------------------------------------------------


def test_init_ledger_tables_creates_both_tables():
    store = _FakeStore()
    ledger_mod.init_ledger_tables(store)
    blob = " ".join(s for s, _ in store.executed)
    assert "props_pick_settled" in blob
    assert "props_tier_ledger" in blob


# ---------------------------------------------------------------------------
# settle_predictions — happy path + idempotency + NaN
# ---------------------------------------------------------------------------


def _pred_df(rows):
    return pd.DataFrame(rows, columns=[
        "game_pk", "market_type", "player_name", "line_value", "side",
        "tier", "american_odds", "predicted_p", "event_date", "actual_value",
    ])


def _empty_settled_df():
    return pd.DataFrame(columns=["game_pk", "market_type", "player_name"])


def test_settle_predictions_classifies_and_writes_new_rows():
    """A LOCK Over win @ +250 → +2.50u; a STRONG Over loss @ -110 → -1u."""
    store = _FakeStore()
    preds = _pred_df([
        # LOCK win: Judge HR Over 0.5, actual=2 HRs hit
        (700001, "HR", "Aaron Judge", 0.5, "Over", "LOCK",
         +250.0, 0.42, "2026-04-15", 2.0),
        # STRONG loss: Crochet K Over 7.5, actual=6
        (700002, "K", "Garrett Crochet", 7.5, "Over", "STRONG",
         -110.0, 0.58, "2026-04-15", 6.0),
        # NO_PLAY skip — never settled
        (700003, "Hits", "Joe Bench", 1.5, "Over", "NO_PLAY",
         -110.0, 0.49, "2026-04-15", 1.0),
    ])
    store.queue_query("FROM prop_predictions p JOIN prop_actuals", preds)
    store.queue_query("FROM props_pick_settled", _empty_settled_df())
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_examined == 3
    assert result.n_picks_settled == 2
    assert result.by_tier[Tier.LOCK] == 1
    assert result.by_tier[Tier.STRONG] == 1

    rows = [u for u in store.upserts if u[0] == "props_pick_settled"][0][1]
    assert len(rows) == 2
    by_pk = {r["game_pk"]: r for r in rows}
    assert by_pk[700001]["actual_hit"] is True
    assert by_pk[700001]["units_delta"] == pytest.approx(2.50)
    assert by_pk[700002]["actual_hit"] is False
    assert by_pk[700002]["units_delta"] == pytest.approx(-1.0)


def test_settle_predictions_is_idempotent():
    store = _FakeStore()
    preds = _pred_df([
        (700001, "HR", "Judge", 0.5, "Over", "LOCK",
         +250.0, 0.42, "2026-04-15", 2.0),
    ])
    settled = pd.DataFrame([
        {"game_pk": 700001, "market_type": "HR", "player_name": "Judge"},
    ])
    store.queue_query("FROM prop_predictions p JOIN prop_actuals", preds)
    store.queue_query("FROM props_pick_settled", settled)

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_already_settled == 1
    assert result.n_picks_settled == 0
    assert not [u for u in store.upserts if u[0] == "props_pick_settled"]


def test_settle_predictions_skips_nan_actuals():
    """Future games will have NULL actual_value rows; settlement must
    skip them silently rather than book them as a loss."""
    store = _FakeStore()
    preds = _pred_df([
        (700001, "HR", "Judge", 0.5, "Over", "LOCK",
         +250.0, 0.42, "2026-04-15", math.nan),
    ])
    store.queue_query("FROM prop_predictions p JOIN prop_actuals", preds)
    store.queue_query("FROM props_pick_settled", _empty_settled_df())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_examined == 1
    assert result.n_picks_settled == 0


def test_settle_predictions_no_qualifying_picks_returns_zero():
    """Predictions exist but all are NO_PLAY tier — no rows written."""
    store = _FakeStore()
    preds = _pred_df([
        (700001, "HR", "X", 0.5, "Over", "NO_PLAY",
         +250.0, 0.20, "2026-04-15", 1.0),
    ])
    store.queue_query("FROM prop_predictions p JOIN prop_actuals", preds)
    store.queue_query("FROM props_pick_settled", _empty_settled_df())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_settled == 0
    assert not [u for u in store.upserts if u[0] == "props_pick_settled"]


def test_settle_predictions_empty_returns_zero():
    store = _FakeStore()
    store.queue_query("FROM prop_predictions p JOIN prop_actuals",
                        _pred_df([]))
    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15",
    )
    assert result.n_picks_examined == 0


# ---------------------------------------------------------------------------
# _refresh_tier_ledger
# ---------------------------------------------------------------------------


def test_refresh_tier_ledger_writes_per_tier_market_and_rollups():
    store = _FakeStore()
    per_tier = pd.DataFrame([
        {"season": 2026, "market_type": "HR", "tier": "LOCK",
         "n_settled": 2, "wins": 2, "losses": 0, "units_won": 5.0},
        {"season": 2026, "market_type": "K", "tier": "STRONG",
         "n_settled": 4, "wins": 2, "losses": 2, "units_won": -0.10},
    ])
    per_market = pd.DataFrame([
        {"season": 2026, "market_type": "HR",
         "n_settled": 2, "wins": 2, "losses": 0, "units_won": 5.0},
        {"season": 2026, "market_type": "K",
         "n_settled": 4, "wins": 2, "losses": 2, "units_won": -0.10},
    ])
    total = pd.DataFrame([
        {"season": 2026, "n_settled": 6, "wins": 4, "losses": 2,
         "units_won": 4.90},
    ])
    store.queue_query("GROUP BY season, market_type, tier", per_tier)
    store.queue_query("GROUP BY season, market_type", per_market)
    store.queue_query("GROUP BY season", total)

    ledger_mod._refresh_tier_ledger(store, season=2026)

    delete_calls = [s for s, _ in store.executed if s.startswith("DELETE")]
    assert any("props_tier_ledger" in s for s in delete_calls)
    writes = [u for u in store.upserts if u[0] == "props_tier_ledger"]
    assert len(writes) == 1
    rows = writes[0][1]
    assert len(rows) == 5  # 2 per-tier + 2 market rollups + 1 total

    rollup_keys = {(r["market_type"], r["tier"]) for r in rows}
    assert ("HR", "ALL") in rollup_keys
    assert ("K", "ALL") in rollup_keys
    assert ("ALL", "ALL") in rollup_keys


def test_refresh_tier_ledger_no_data_writes_nothing():
    store = _FakeStore()
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())
    ledger_mod._refresh_tier_ledger(store, season=2026)
    assert not [u for u in store.upserts if u[0] == "props_tier_ledger"]


# ---------------------------------------------------------------------------
# get_tier_ledger / render_ledger_section
# ---------------------------------------------------------------------------


def _ledger_df(rows):
    return pd.DataFrame(rows, columns=[
        "season", "market_type", "tier",
        "n_settled", "wins", "losses", "units_won", "last_updated",
    ])


def test_get_tier_ledger_orders_by_market_then_tier():
    store = _FakeStore()
    rows = _ledger_df([
        (2026, "K",   "STRONG", 4, 2, 2, -0.10, None),
        (2026, "HR",  "LOCK",   2, 2, 0, 5.00, None),
        (2026, "HR",  "ALL",    2, 2, 0, 5.00, None),
        (2026, "ALL", "ALL",    6, 4, 2, 4.90, None),
    ])
    store.queue_query("FROM props_tier_ledger", rows)

    df = ledger_mod.get_tier_ledger(store, 2026)
    markets = list(df["market_type"])
    tiers = list(df["tier"])
    # HR rows first (earlier in market_order), with LOCK before ALL
    assert markets[0] == "HR"
    assert tiers[0] == "LOCK"
    assert markets[2] == "K"
    assert markets[3] == "ALL"


def test_get_tier_ledger_empty_returns_empty():
    store = _FakeStore()
    store.queue_query("FROM props_tier_ledger", pd.DataFrame())
    out = ledger_mod.get_tier_ledger(store, 2026)
    assert out is None or out.empty


def test_render_ledger_section_empty_returns_empty_string():
    store = _FakeStore()
    store.queue_query("FROM props_tier_ledger", pd.DataFrame())
    assert ledger_mod.render_ledger_section(store, 2026) == ""


def test_render_ledger_section_formats_record_units_roi():
    store = _FakeStore()
    rows = _ledger_df([
        (2026, "HR", "LOCK", 4, 4, 0, 7.50, None),
        (2026, "K",  "STRONG", 6, 4, 2, 0.20, None),
    ])
    store.queue_query("FROM props_tier_ledger", rows)
    text = ledger_mod.render_ledger_section(store, 2026)
    assert "PROPS YTD LEDGER (2026)" in text
    assert "HR" in text
    assert "LOCK" in text
    assert "4-0" in text
    assert "4-2" in text
    assert "+7.50u" in text
