"""Tests for the NRFI per-tier YTD ledger (Phase 3).

The ledger module persists settled NRFI/YRFI picks with their tier
classification and aggregates running W/L + units per tier. These
tests exercise the pure-Python pieces directly and use a duck-typed
fake store for the SQL-driven settlement / aggregation paths so the
suite stays runnable without DuckDB installed.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from edge_equation.engines.nrfi import ledger as ledger_mod
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Pure-Python helpers
# ---------------------------------------------------------------------------


def test_pick_payout_units_minus_120():
    """1u risked at -120 wins 0.8333u (decimal 1.8333 − 1)."""
    payout = ledger_mod._pick_payout_units(-120.0)
    assert payout == pytest.approx(100.0 / 120.0)


def test_pick_payout_units_minus_105():
    """1u at -105 wins 0.9524u."""
    payout = ledger_mod._pick_payout_units(-105.0)
    assert payout == pytest.approx(100.0 / 105.0)


def test_pick_payout_units_plus_money():
    """1u at +120 wins 1.20u."""
    payout = ledger_mod._pick_payout_units(+120.0)
    assert payout == pytest.approx(1.20)


# ---------------------------------------------------------------------------
# Side selection — pick the higher-tier side
# ---------------------------------------------------------------------------


def test_stake_side_picks_nrfi_when_nrfi_is_strong():
    market, side_p, odds = ledger_mod._stake_side_for_game(0.72)
    assert market == "NRFI"
    assert side_p == pytest.approx(0.72)
    assert odds == ledger_mod.DEFAULT_NRFI_ODDS


def test_stake_side_picks_yrfi_when_yrfi_is_strong():
    """nrfi_prob = 0.30 → yrfi_prob = 0.70 (LOCK on the YRFI side)."""
    market, side_p, odds = ledger_mod._stake_side_for_game(0.30)
    assert market == "YRFI"
    assert side_p == pytest.approx(0.70)
    assert odds == ledger_mod.DEFAULT_YRFI_ODDS


def test_stake_side_breaks_ties_toward_nrfi():
    """At 0.50/0.50 both sides are NO_PLAY; selector returns NRFI."""
    market, side_p, _ = ledger_mod._stake_side_for_game(0.50)
    assert market == "NRFI"
    assert side_p == pytest.approx(0.50)


def test_stake_side_subqualifying_still_returns_a_side():
    """0.56 NRFI (LEAN) vs 0.44 YRFI (NO_PLAY) — NRFI wins."""
    market, side_p, _ = ledger_mod._stake_side_for_game(0.56)
    assert market == "NRFI"
    assert side_p == pytest.approx(0.56)


# ---------------------------------------------------------------------------
# SettlementResult formatting
# ---------------------------------------------------------------------------


def test_settlement_result_defaults_initialise_by_tier():
    r = ledger_mod.SettlementResult()
    assert r.by_tier is not None
    for t in Tier:
        assert r.by_tier[t] == 0


def test_settlement_result_summary_contains_counts():
    r = ledger_mod.SettlementResult(
        n_picks_examined=10,
        n_picks_already_settled=3,
        n_picks_settled=5,
        n_picks_no_actual=2,
    )
    r.by_tier[Tier.LOCK] = 2
    r.by_tier[Tier.STRONG] = 3
    summary = r.summary()
    assert "picks examined" in summary
    assert "10" in summary
    assert "newly settled" in summary
    assert "5" in summary
    assert "LOCK" in summary
    assert "STRONG" in summary
    # Tiers with zero counts should not be rendered.
    assert "MODERATE" not in summary


def test_settlement_result_summary_omits_per_tier_when_nothing_settled():
    r = ledger_mod.SettlementResult(
        n_picks_examined=2, n_picks_no_actual=2,
    )
    summary = r.summary()
    assert "by tier" not in summary


# ---------------------------------------------------------------------------
# Fake DuckDB-like store for SQL-driven paths
# ---------------------------------------------------------------------------


class _FakeStore:
    """Just-enough NRFIStore stand-in for the ledger SQL paths.

    Intercepts query_df by string-matching, records upserts and executes,
    and lets each test pre-stage the dataframes it expects to be returned.
    """

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.upserts: list[tuple[str, list[dict]]] = []
        # Queue of (sql_substring, df) pairs to match in order.
        self.query_responses: list[tuple[str, pd.DataFrame]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql.strip(), tuple(params or ())))

    def upsert(self, table: str, rows) -> int:
        rows = list(rows)
        self.upserts.append((table, rows))
        return len(rows)

    def queue_query(self, sql_substring: str, df: pd.DataFrame) -> None:
        self.query_responses.append((sql_substring, df))

    def query_df(self, sql: str, params: tuple = ()):
        # Find the first queued response whose substring matches.
        normalised = " ".join(sql.split())
        for i, (needle, df) in enumerate(self.query_responses):
            if needle in normalised:
                self.query_responses.pop(i)
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
    sql_blob = " ".join(s for s, _ in store.executed)
    assert "nrfi_pick_settled" in sql_blob
    assert "nrfi_tier_ledger" in sql_blob


# ---------------------------------------------------------------------------
# settle_predictions — happy path + idempotency
# ---------------------------------------------------------------------------


def _predictions_df(rows):
    """Helper to build the JOINed predictions×actuals×games frame."""
    return pd.DataFrame(rows, columns=[
        "game_pk", "nrfi_prob", "lambda_total",
        "game_date", "season", "actual_nrfi", "first_inn_runs",
    ])


def _empty_settled_df():
    return pd.DataFrame(columns=["game_pk", "market_type"])


def test_settle_predictions_classifies_and_writes_new_rows(monkeypatch):
    """Three games: a LOCK NRFI win, a STRONG NRFI loss, and a sub-LEAN
    skip. The LEAN floor is enforced — NO_PLAY rows must not be written."""
    store = _FakeStore()

    preds = _predictions_df([
        # LOCK NRFI win  (nrfi_prob 0.85, actual_nrfi=True)
        (1001, 0.85, 0.4, "2026-04-15", 2026, True, 0),
        # STRONG NRFI loss (nrfi_prob 0.66, actual_nrfi=False)
        (1002, 0.66, 0.6, "2026-04-15", 2026, False, 1),
        # NO_PLAY (nrfi_prob 0.50 — both sides sub-LEAN)
        (1003, 0.50, 1.0, "2026-04-15", 2026, True, 0),
    ])
    store.queue_query("FROM predictions p", preds)
    store.queue_query("FROM nrfi_pick_settled", _empty_settled_df())
    # _refresh_tier_ledger queries (per-tier, per-market, total).
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    # Skip the network call.
    monkeypatch.setattr(ledger_mod, "backfill_actuals",
                          lambda *a, **kw: 0)

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    assert result.n_picks_examined == 3
    assert result.n_picks_settled == 2
    assert result.by_tier[Tier.LOCK] == 1
    assert result.by_tier[Tier.STRONG] == 1
    # NO_PLAY game is unsettled but counted as "no actual" residual.
    assert result.n_picks_no_actual == 1

    # Inspect the rows handed to upsert.
    settled_writes = [u for u in store.upserts if u[0] == "nrfi_pick_settled"]
    assert len(settled_writes) == 1
    rows = settled_writes[0][1]
    assert len(rows) == 2
    by_pk = {r["game_pk"]: r for r in rows}
    assert by_pk[1001]["tier"] == "LOCK"
    assert by_pk[1001]["market_type"] == "NRFI"
    assert by_pk[1001]["actual_hit"] is True
    assert by_pk[1001]["units_delta"] == pytest.approx(100.0 / 120.0)
    assert by_pk[1002]["tier"] == "STRONG"
    assert by_pk[1002]["actual_hit"] is False
    assert by_pk[1002]["units_delta"] == pytest.approx(-1.0)


def test_settle_predictions_yrfi_side_when_nrfi_is_low():
    """nrfi_prob 0.25 → YRFI side at 0.75 (LOCK). Hit when actual_nrfi=False."""
    store = _FakeStore()
    preds = _predictions_df([
        (2001, 0.25, 1.5, "2026-04-15", 2026, False, 1),  # YRFI LOCK win
    ])
    store.queue_query("FROM predictions p", preds)
    store.queue_query("FROM nrfi_pick_settled", _empty_settled_df())
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    assert result.n_picks_settled == 1
    assert result.by_tier[Tier.LOCK] == 1
    rows = [u for u in store.upserts if u[0] == "nrfi_pick_settled"][0][1]
    assert rows[0]["market_type"] == "YRFI"
    assert rows[0]["actual_hit"] is True
    assert rows[0]["american_odds"] == pytest.approx(-105.0)
    assert rows[0]["predicted_p"] == pytest.approx(0.75)


def test_settle_predictions_is_idempotent():
    """Re-running settlement on a fully-settled set must not re-insert."""
    store = _FakeStore()
    preds = _predictions_df([
        (3001, 0.85, 0.3, "2026-04-15", 2026, True, 0),
    ])
    settled = pd.DataFrame([{"game_pk": 3001, "market_type": "NRFI"}])
    store.queue_query("FROM predictions p", preds)
    store.queue_query("FROM nrfi_pick_settled", settled)

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    assert result.n_picks_examined == 1
    assert result.n_picks_already_settled == 1
    assert result.n_picks_settled == 0
    # No new upsert should have happened (the refresh is also skipped).
    assert not [u for u in store.upserts if u[0] == "nrfi_pick_settled"]


def test_settle_predictions_skips_nan_probabilities():
    """Predictions whose nrfi_prob is NaN must be skipped silently."""
    store = _FakeStore()
    preds = _predictions_df([
        (4001, math.nan, 1.0, "2026-04-15", 2026, True, 0),
    ])
    store.queue_query("FROM predictions p", preds)
    store.queue_query("FROM nrfi_pick_settled", _empty_settled_df())

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    assert result.n_picks_examined == 1
    assert result.n_picks_settled == 0
    assert not [u for u in store.upserts if u[0] == "nrfi_pick_settled"]


def test_settle_predictions_empty_predictions_returns_zero():
    store = _FakeStore()
    store.queue_query("FROM predictions p", _predictions_df([]))

    result = ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    assert result.n_picks_examined == 0
    assert result.n_picks_settled == 0


# ---------------------------------------------------------------------------
# _refresh_tier_ledger — aggregation rollups
# ---------------------------------------------------------------------------


def test_refresh_tier_ledger_writes_per_tier_market_and_rollups():
    store = _FakeStore()
    per_tier = pd.DataFrame([
        {"season": 2026, "market_type": "NRFI", "tier": "LOCK",
         "n_settled": 4, "wins": 3, "losses": 1, "units_won": 1.5},
        {"season": 2026, "market_type": "NRFI", "tier": "STRONG",
         "n_settled": 6, "wins": 3, "losses": 3, "units_won": -0.5},
        {"season": 2026, "market_type": "YRFI", "tier": "LOCK",
         "n_settled": 2, "wins": 2, "losses": 0, "units_won": 1.9},
    ])
    per_market = pd.DataFrame([
        {"season": 2026, "market_type": "NRFI",
         "n_settled": 10, "wins": 6, "losses": 4, "units_won": 1.0},
        {"season": 2026, "market_type": "YRFI",
         "n_settled": 2, "wins": 2, "losses": 0, "units_won": 1.9},
    ])
    total = pd.DataFrame([
        {"season": 2026, "n_settled": 12, "wins": 8, "losses": 4,
         "units_won": 2.9},
    ])
    store.queue_query("GROUP BY season, market_type, tier", per_tier)
    store.queue_query("GROUP BY season, market_type", per_market)
    store.queue_query("GROUP BY season", total)

    ledger_mod._refresh_tier_ledger(store, season=2026)

    # The DELETE before reinsert.
    delete_calls = [s for s, _ in store.executed if s.startswith("DELETE")]
    assert any("nrfi_tier_ledger" in s for s in delete_calls)

    # 3 per-tier rows + 2 market rollups + 1 total = 6 rows.
    writes = [u for u in store.upserts if u[0] == "nrfi_tier_ledger"]
    assert len(writes) == 1
    rows = writes[0][1]
    assert len(rows) == 6

    # Roll-up rows must use the "ALL" sentinel.
    rollup_keys = {(r["market_type"], r["tier"]) for r in rows}
    assert ("NRFI", "ALL") in rollup_keys
    assert ("YRFI", "ALL") in rollup_keys
    assert ("ALL", "ALL") in rollup_keys


def test_refresh_tier_ledger_no_data_writes_nothing():
    store = _FakeStore()
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    ledger_mod._refresh_tier_ledger(store, season=2026)

    # DELETE still runs (idempotent reset), but no upsert.
    assert not [u for u in store.upserts if u[0] == "nrfi_tier_ledger"]


# ---------------------------------------------------------------------------
# get_tier_ledger / render_ledger_section
# ---------------------------------------------------------------------------


def _ledger_df(rows):
    return pd.DataFrame(rows, columns=[
        "season", "market_type", "tier",
        "n_settled", "wins", "losses", "units_won", "last_updated",
    ])


def test_get_tier_ledger_orders_nrfi_first_then_lock_first():
    store = _FakeStore()
    rows = _ledger_df([
        (2026, "YRFI", "STRONG", 3, 2, 1, 0.5, None),
        (2026, "NRFI", "STRONG", 5, 3, 2, 0.4, None),
        (2026, "NRFI", "LOCK",   4, 4, 0, 3.3, None),
        (2026, "NRFI", "ALL",    9, 7, 2, 3.7, None),
    ])
    store.queue_query("FROM nrfi_tier_ledger", rows)

    df = ledger_mod.get_tier_ledger(store, 2026)

    # NRFI rows come before YRFI; within NRFI, LOCK before STRONG before ALL.
    markets = list(df["market_type"])
    tiers = list(df["tier"])
    assert markets[:3] == ["NRFI", "NRFI", "NRFI"]
    assert markets[3] == "YRFI"
    assert tiers[:3] == ["LOCK", "STRONG", "ALL"]


def test_get_tier_ledger_empty_returns_empty():
    store = _FakeStore()
    store.queue_query("FROM nrfi_tier_ledger", pd.DataFrame())
    out = ledger_mod.get_tier_ledger(store, 2026)
    assert out is None or out.empty


def test_render_ledger_section_empty_returns_empty_string():
    store = _FakeStore()
    store.queue_query("FROM nrfi_tier_ledger", pd.DataFrame())
    assert ledger_mod.render_ledger_section(store, 2026) == ""


def test_render_ledger_section_formats_records_and_units():
    store = _FakeStore()
    rows = _ledger_df([
        (2026, "NRFI", "LOCK",   4, 4, 0, 3.33, None),
        (2026, "NRFI", "STRONG", 5, 3, 2, 0.50, None),
    ])
    store.queue_query("FROM nrfi_tier_ledger", rows)

    text = ledger_mod.render_ledger_section(store, 2026)

    assert "YTD LEDGER (2026)" in text
    assert "NRFI" in text
    assert "LOCK" in text
    assert "4-0" in text          # wins-losses for the LOCK row
    assert "3-2" in text          # wins-losses for the STRONG row
    assert "+3.33u" in text       # signed units format
    assert "+0.50u" in text


# ---------------------------------------------------------------------------
# Phase 4: live closing-odds path
# ---------------------------------------------------------------------------


def test_settle_uses_captured_closing_odds_when_available(monkeypatch):
    """When a closing-line snapshot exists for the staked side, the
    settled row uses the captured american_odds and computes payout
    against it instead of the -120/-105 default."""
    store = _FakeStore()
    preds = _predictions_df([
        # LOCK NRFI win — but the closing line was actually -150,
        # not the default -120, so units_delta should reflect the worse
        # price (lower payout per win).
        (5001, 0.85, 0.4, "2026-04-15", 2026, True, 0),
    ])
    store.queue_query("FROM predictions p", preds)
    store.queue_query("FROM nrfi_pick_settled", _empty_settled_df())
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    monkeypatch.setattr(
        ledger_mod, "_lookup_closing_odds_safe",
        lambda *a, **kw: -150.0,
    )

    ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    rows = [u for u in store.upserts if u[0] == "nrfi_pick_settled"][0][1]
    assert len(rows) == 1
    assert rows[0]["american_odds"] == pytest.approx(-150.0)
    # 1u win at -150 = 100/150 = 0.6667u, NOT the -120 default's 0.8333u.
    assert rows[0]["units_delta"] == pytest.approx(100.0 / 150.0)


def test_settle_falls_back_to_default_odds_when_no_snapshot(monkeypatch):
    """No captured snapshot → default -120/-105 odds path. Confirms the
    lookup never blocks settlement even when the odds module errors out."""
    store = _FakeStore()
    preds = _predictions_df([
        (6001, 0.85, 0.4, "2026-04-15", 2026, True, 0),
    ])
    store.queue_query("FROM predictions p", preds)
    store.queue_query("FROM nrfi_pick_settled", _empty_settled_df())
    store.queue_query("GROUP BY season, market_type, tier", pd.DataFrame())
    store.queue_query("GROUP BY season, market_type", pd.DataFrame())
    store.queue_query("GROUP BY season", pd.DataFrame())

    monkeypatch.setattr(
        ledger_mod, "_lookup_closing_odds_safe",
        lambda *a, **kw: None,
    )

    ledger_mod.settle_predictions(
        store, season=2026, cutoff_date="2026-04-15", pull_actuals=False,
    )

    rows = [u for u in store.upserts if u[0] == "nrfi_pick_settled"][0][1]
    assert rows[0]["american_odds"] == pytest.approx(
        ledger_mod.DEFAULT_NRFI_ODDS,
    )
    assert rows[0]["units_delta"] == pytest.approx(100.0 / 120.0)


def test_lookup_closing_odds_safe_swallows_module_errors(monkeypatch):
    """If the odds module raises, settlement must still see None (not
    the exception). Simulates the case where the odds table doesn't
    exist yet on a fresh DB."""
    def _boom(*a, **kw):
        raise RuntimeError("odds table missing")

    # Force `from .data.odds import lookup_closing_odds` to resolve to
    # the boom function by monkey-patching the module attribute.
    from edge_equation.engines.nrfi.data import odds as odds_mod
    monkeypatch.setattr(odds_mod, "lookup_closing_odds", _boom)

    out = ledger_mod._lookup_closing_odds_safe(_FakeStore(), 1, "NRFI")
    assert out is None
