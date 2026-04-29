"""Tests for the units-only parlay ledger."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from edge_equation.engines.parlay import (
    ParlayLeg,
    build_parlay_candidates,
    init_parlay_tables,
    record_parlay,
    render_ledger_section,
    settle_parlay,
)
from edge_equation.engines.parlay import ledger as ledger_mod
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Fake DuckDB-like store
# ---------------------------------------------------------------------------


class _FakeStore:
    """In-memory dict keyed by parlay_id, supporting the SQL the
    ledger module emits: CREATE / INSERT OR REPLACE / SELECT / UPDATE."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.upserts: list[tuple[str, list[dict]]] = []
        self._rows: dict[str, dict] = {}

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql.strip(), tuple(params or ())))
        normalised = " ".join(sql.split()).upper()
        if normalised.startswith("UPDATE PARLAY_LEDGER"):
            return_units, settled_at, parlay_id = params
            if parlay_id in self._rows:
                self._rows[parlay_id]["return_units"] = return_units
                self._rows[parlay_id]["settled_at"] = settled_at

    def upsert(self, table: str, rows) -> int:
        rows = list(rows)
        self.upserts.append((table, rows))
        if table == "parlay_ledger":
            for r in rows:
                self._rows[r["parlay_id"]] = dict(r)
        return len(rows)

    def query_df(self, sql: str, params: tuple = ()):
        normalised = " ".join(sql.split()).upper()
        if normalised.startswith("SELECT") and "WHERE PARLAY_ID = ?" in normalised:
            (pid,) = params
            row = self._rows.get(pid)
            if row is None:
                return pd.DataFrame()
            return pd.DataFrame([row])
        if "FROM PARLAY_LEDGER" in normalised:
            if not self._rows:
                return pd.DataFrame()
            df = pd.DataFrame(list(self._rows.values()))
            return df.sort_values("recorded_at", ascending=False)\
                .reset_index(drop=True)
        raise AssertionError(f"unexpected query: {normalised!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leg(market="NRFI", prob=0.85, odds=-115, tier=Tier.ELITE,
          game="g1", label="leg"):
    return ParlayLeg(
        market_type=market, side="Under 0.5",
        side_probability=prob, american_odds=odds, tier=tier,
        game_id=game, label=label,
    )


def _two_lock_nrfi_candidate():
    legs = [
        _leg(prob=0.85, odds=-115, game="g1", label="g1 NRFI"),
        _leg(prob=0.84, odds=-110, game="g2", label="g2 NRFI"),
    ]
    cands = build_parlay_candidates(legs)
    assert cands, "expected at least one candidate"
    return cands[0]


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------


def test_init_parlay_tables_creates_table():
    store = _FakeStore()
    init_parlay_tables(store)
    sql_blob = " ".join(s for s, _ in store.executed)
    assert "parlay_ledger" in sql_blob


# ---------------------------------------------------------------------------
# record_parlay
# ---------------------------------------------------------------------------


def test_record_parlay_writes_legs_as_json_and_returns_id():
    store = _FakeStore()
    cand = _two_lock_nrfi_candidate()
    pid = record_parlay(store, cand, notes="opening week LOCK stack")
    assert isinstance(pid, str) and len(pid) > 0
    upserts = [u for u in store.upserts if u[0] == "parlay_ledger"]
    assert len(upserts) == 1
    row = upserts[0][1][0]
    assert row["parlay_id"] == pid
    legs_back = json.loads(row["legs_json"])
    assert len(legs_back) == 2
    assert legs_back[0]["market_type"] == "NRFI"
    assert legs_back[0]["tier"] == "ELITE"
    assert row["stake_units"] == pytest.approx(0.5)
    assert row["return_units"] is None       # not yet settled
    assert row["settled_at"] is None
    assert row["notes"] == "opening week LOCK stack"


def test_record_parlay_uses_explicit_id_when_provided():
    store = _FakeStore()
    cand = _two_lock_nrfi_candidate()
    pid = record_parlay(store, cand, parlay_id="ticket-001")
    assert pid == "ticket-001"


def test_record_parlay_handles_inf_fair_decimal():
    """Defensive: fair_decimal_odds is ∞ when joint_prob_corr is 0.
    The ledger should write NULL rather than a bogus float."""
    from edge_equation.engines.parlay.builder import ParlayCandidate
    cand = ParlayCandidate(
        legs=tuple(),
        joint_prob_independent=0.0,
        joint_prob_corr=0.0,
        fair_decimal_odds=float("inf"),
        combined_decimal_odds=4.0,
        implied_prob=0.25,
        ev_units=-0.5,
        stake_units=0.5,
    )
    store = _FakeStore()
    record_parlay(store, cand)
    row = store.upserts[0][1][0]
    assert row["fair_decimal_odds"] is None


# ---------------------------------------------------------------------------
# settle_parlay
# ---------------------------------------------------------------------------


def test_settle_parlay_pays_full_combined_when_all_hit():
    store = _FakeStore()
    cand = _two_lock_nrfi_candidate()
    pid = record_parlay(store, cand)
    expected_payout = (cand.combined_decimal_odds - 1.0) * cand.stake_units
    ru = settle_parlay(store, pid, leg_outcomes=[True, True])
    assert ru == pytest.approx(expected_payout)
    assert store._rows[pid]["return_units"] == pytest.approx(expected_payout)
    assert store._rows[pid]["settled_at"] is not None


def test_settle_parlay_loses_stake_on_any_miss():
    store = _FakeStore()
    cand = _two_lock_nrfi_candidate()
    pid = record_parlay(store, cand)
    ru = settle_parlay(store, pid, leg_outcomes=[True, False])
    assert ru == pytest.approx(-cand.stake_units)
    assert store._rows[pid]["return_units"] == pytest.approx(-cand.stake_units)


def test_settle_parlay_raises_on_unknown_id():
    store = _FakeStore()
    init_parlay_tables(store)
    with pytest.raises(KeyError):
        settle_parlay(store, "no-such-id", leg_outcomes=[True])


def test_settle_parlay_rejects_wrong_outcome_count():
    store = _FakeStore()
    cand = _two_lock_nrfi_candidate()
    pid = record_parlay(store, cand)
    with pytest.raises(ValueError):
        settle_parlay(store, pid, leg_outcomes=[True])     # 1 vs 2


# ---------------------------------------------------------------------------
# render_ledger_section
# ---------------------------------------------------------------------------


def test_render_ledger_empty_returns_empty_string():
    store = _FakeStore()
    init_parlay_tables(store)
    assert render_ledger_section(store) == ""


def test_render_ledger_aggregates_units_and_roi():
    store = _FakeStore()
    init_parlay_tables(store)
    cand = _two_lock_nrfi_candidate()
    p1 = record_parlay(store, cand, notes="first")
    p2 = record_parlay(store, cand, notes="second")
    settle_parlay(store, p1, leg_outcomes=[True, True])
    settle_parlay(store, p2, leg_outcomes=[True, False])

    text = render_ledger_section(store)

    assert "PARLAY LEDGER" in text
    assert "tickets recorded" in text
    assert "settled" in text
    assert "units returned" in text
    assert "ROI" in text


def test_render_ledger_pending_count_matches_unsettled_rows():
    store = _FakeStore()
    init_parlay_tables(store)
    cand = _two_lock_nrfi_candidate()
    p1 = record_parlay(store, cand)
    p2 = record_parlay(store, cand)
    settle_parlay(store, p1, leg_outcomes=[True, True])
    text = render_ledger_section(store)
    assert "tickets recorded   2" in text
    assert "settled            1" in text
    assert "pending            1" in text
