"""Tests for the track-record exporter (engine ledgers → website JSON).

Synthetic input only — fakes the NRFIStore so we don't need DuckDB.
Verifies the aggregation math, tier filtering (NO_PLAY excluded),
result-label mapping, and JSON shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake store — the exporter calls store.query_df(sql) for each engine.
# ---------------------------------------------------------------------------


class _FakeStore:
    """In-memory map of SQL → DataFrame. Tables that aren't in the map
    raise to simulate _table_exists()'s probe."""

    def __init__(self, tables: dict[str, list[dict]] | None = None):
        # `tables` is a map of table-name → list of row dicts. The
        # exporter hits `SELECT 1 FROM <table> LIMIT 1` first; we
        # raise for missing tables so _table_exists returns False.
        self._tables = tables or {}

    def query_df(self, sql: str, params: tuple = ()):
        import pandas as pd
        # Simulate _table_exists probe: "SELECT 1 FROM <table> LIMIT 1"
        if "LIMIT 1" in sql and "FROM" in sql:
            for tbl in self._tables:
                if f"FROM {tbl}" in sql:
                    return pd.DataFrame([{"col": 1}])
            raise RuntimeError(f"table not found in fake: {sql[:80]}")
        # Otherwise — engine query. Match by table presence.
        for tbl, rows in self._tables.items():
            if tbl in sql:
                return pd.DataFrame(rows)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Tier filter — NO_PLAY must be excluded
# ---------------------------------------------------------------------------


def test_no_play_picks_are_excluded_from_ledger():
    pytest.importorskip("pandas")
    from edge_equation.engines.website.build_track_record import (
        _load_engine_rows,
    )

    rows_in = [
        _make_nrfi_row(tier="ELITE"),
        _make_nrfi_row(tier="STRONG"),
        _make_nrfi_row(tier="MODERATE"),
        _make_nrfi_row(tier="LEAN"),
        _make_nrfi_row(tier="NO_PLAY"),  # should drop
        _make_nrfi_row(tier="GARBAGE"),  # should drop
    ]
    store = _FakeStore({"nrfi_pick_settled": rows_in})
    out = _load_engine_rows(store, "nrfi")
    tiers = {r.tier for r in out}
    assert tiers == {"ELITE", "STRONG", "MODERATE", "LEAN"}
    assert len(out) == 4


def test_lower_case_tier_strings_are_normalized():
    pytest.importorskip("pandas")
    from edge_equation.engines.website.build_track_record import (
        _load_engine_rows,
    )

    store = _FakeStore({"nrfi_pick_settled": [
        _make_nrfi_row(tier="elite"),
        _make_nrfi_row(tier="Strong"),
    ]})
    out = _load_engine_rows(store, "nrfi")
    assert {r.tier for r in out} == {"ELITE", "STRONG"}


# ---------------------------------------------------------------------------
# Aggregation math
# ---------------------------------------------------------------------------


def test_aggregate_summary_counts_wins_losses_pushes_correctly():
    from edge_equation.engines.website.build_track_record import (
        LedgerRow, aggregate_summary,
    )
    rows = [
        _ledger_row(units_delta=0.91),    # win
        _ledger_row(units_delta=0.91),    # win
        _ledger_row(units_delta=-1.00),   # loss
        _ledger_row(units_delta=0.0),     # push
    ]
    out = aggregate_summary(rows)
    assert len(out) == 1
    s = out[0]
    assert s.n_settled == 4
    assert s.wins == 2
    assert s.losses == 1
    assert s.pushes == 1
    # Hit rate = wins / (wins + losses), excludes pushes
    assert abs(s.hit_rate - (2 / 3)) < 1e-9


def test_aggregate_summary_skips_pending_rows():
    """actual_hit=None means the game hasn't completed. Those don't
    feed the running record — only settled outcomes do."""
    from edge_equation.engines.website.build_track_record import (
        aggregate_summary,
    )
    rows = [
        _ledger_row(units_delta=0.91, actual_hit=True),    # settled, win
        _ledger_row(units_delta=0.0, actual_hit=None),     # pending
    ]
    out = aggregate_summary(rows)
    assert out[0].n_settled == 1
    assert out[0].wins == 1


def test_aggregate_by_day_groups_across_engines():
    from edge_equation.engines.website.build_track_record import (
        aggregate_by_day,
    )
    rows = [
        _ledger_row(engine="nrfi", units_delta=0.91, settled_at="2026-04-30T18:00:00"),
        _ledger_row(engine="full_game", units_delta=-1.0, settled_at="2026-04-30T22:00:00"),
        _ledger_row(engine="nrfi", units_delta=0.91, settled_at="2026-05-01T18:00:00"),
    ]
    out = aggregate_by_day(rows)
    assert len(out) == 2
    by_date = {d.date: d for d in out}
    assert by_date["2026-04-30"].n_settled == 2
    assert by_date["2026-04-30"].wins == 1
    assert by_date["2026-04-30"].losses == 1
    assert by_date["2026-05-01"].wins == 1


# ---------------------------------------------------------------------------
# Result label mapping
# ---------------------------------------------------------------------------


def test_result_label_maps_units_to_w_l_push():
    from edge_equation.engines.website.build_track_record import (
        _result_label, LedgerRow,
    )
    assert _result_label(_ledger_row(units_delta=0.91)) == "W"
    assert _result_label(_ledger_row(units_delta=-1.0)) == "L"
    assert _result_label(_ledger_row(units_delta=0.0)) == "Push"
    assert _result_label(_ledger_row(actual_hit=None)) == "Pending"


# ---------------------------------------------------------------------------
# JSON output shape
# ---------------------------------------------------------------------------


def test_write_bundle_produces_three_files(tmp_path: Path):
    from edge_equation.engines.website.build_track_record import (
        TrackRecordBundle, aggregate_by_day, aggregate_summary, write_bundle,
    )
    rows = [
        _ledger_row(units_delta=0.91, settled_at="2026-05-01T19:00:00"),
        _ledger_row(units_delta=-1.0, settled_at="2026-05-01T19:00:00"),
    ]
    bundle = TrackRecordBundle(
        generated_at="2026-05-01T20:00:00+00:00",
        ledger=rows,
        summary=aggregate_summary(rows),
        by_day=aggregate_by_day(rows),
    )
    write_bundle(bundle, tmp_path)

    for fname in ("ledger.json", "summary.json", "by-day.json"):
        assert (tmp_path / fname).exists(), f"missing {fname}"
        payload = json.loads((tmp_path / fname).read_text())
        assert payload["version"] == 1
        assert payload["generated_at"] == "2026-05-01T20:00:00+00:00"


def test_ledger_json_has_human_readable_pct_and_result_columns(tmp_path: Path):
    from edge_equation.engines.website.build_track_record import (
        TrackRecordBundle, write_bundle,
    )
    bundle = TrackRecordBundle(
        generated_at="2026-05-01T20:00:00+00:00",
        ledger=[_ledger_row(predicted_p=0.673, units_delta=0.91)],
        summary=[],
        by_day=[],
    )
    write_bundle(bundle, tmp_path)
    payload = json.loads((tmp_path / "ledger.json").read_text())
    pick = payload["picks"][0]
    assert pick["predicted_pct"] == 67.3      # 0.673 -> 67.3%
    assert pick["result"] == "W"
    assert "predicted_p" in pick                # also keep the raw 0..1 form


def test_missing_engine_table_does_not_error(tmp_path: Path):
    """Fresh DuckDB might not have every engine's tables yet. The
    exporter should silently skip and still write valid JSON for the
    engines that DO have data."""
    pytest.importorskip("pandas")
    from edge_equation.engines.website.build_track_record import (
        build_bundle, write_bundle,
    )
    # Only nrfi_pick_settled is populated; the others are absent.
    store = _FakeStore({"nrfi_pick_settled": [_make_nrfi_row(tier="ELITE")]})
    bundle = build_bundle(store)
    write_bundle(bundle, tmp_path)
    payload = json.loads((tmp_path / "ledger.json").read_text())
    assert payload["n_picks"] == 1


# ---------------------------------------------------------------------------
# Tier registry
# ---------------------------------------------------------------------------


def test_lean_and_above_constant_excludes_no_play():
    from edge_equation.engines.website.build_track_record import LEAN_AND_ABOVE
    assert "NO_PLAY" not in LEAN_AND_ABOVE
    assert "ELITE" in LEAN_AND_ABOVE
    assert len(LEAN_AND_ABOVE) == 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nrfi_row(*, tier: str = "ELITE", **overrides: Any) -> dict:
    """A single nrfi_pick_settled-shaped row for the fake store. The
    fake's query_df returns whatever dicts we pass; column names need
    to match the SELECT alias names in build_track_record._NRFI_QUERY."""
    base = {
        "engine": "nrfi",
        "sport": "MLB",
        "market_type": "NRFI",
        "pick_label": "Yankees @ Red Sox",
        "season": 2026,
        "tier": tier,
        "predicted_p": 0.673,
        "american_odds": -120.0,
        "actual_hit": True,
        "units_delta": 0.83,
        "settled_at": "2026-05-01T19:30:00",
    }
    base.update(overrides)
    return base


def _ledger_row(**overrides: Any):
    """Construct a LedgerRow directly. Useful for aggregation tests
    that don't need to hit the SQL path."""
    from edge_equation.engines.website.build_track_record import LedgerRow
    base = dict(
        engine="nrfi",
        sport="MLB",
        market_type="NRFI",
        pick_label="A @ B",
        season=2026,
        tier="ELITE",
        predicted_p=0.65,
        american_odds=-120.0,
        actual_hit=True,
        units_delta=0.83,
        settled_at="2026-05-01T19:30:00",
    )
    base.update(overrides)
    return LedgerRow(**base)
