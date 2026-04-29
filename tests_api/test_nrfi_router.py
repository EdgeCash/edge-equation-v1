"""API tests for the NRFI router.

Lives under `tests_api/` because it depends on fastapi (which is
provisioned in the api-test workflow but NOT in the lighter
`Tests` workflow used for `tests/`).
"""

from __future__ import annotations

import importlib
import sys


def test_nrfi_router_today_returns_list_or_empty():
    """The route must return a list (empty when extras absent) — never raise."""
    from api.routers.nrfi import get_nrfi_today
    out = get_nrfi_today()
    assert isinstance(out, list)


def test_nrfi_router_board_handles_missing_data():
    from api.routers.nrfi import get_nrfi_board
    out = get_nrfi_board(date="1999-01-01")
    assert isinstance(out, list)
    assert out == []


def test_api_main_mounts_nrfi_router():
    # Reload api.main to ensure import-time wiring is exercised.
    if "api.main" in sys.modules:
        del sys.modules["api.main"]
    main = importlib.import_module("api.main")
    paths = {r.path for r in main.app.routes}
    assert "/nrfi/today" in paths
    assert "/nrfi/board" in paths


def test_nrfi_today_route_via_test_client(client):
    """Smoke-test the route through the FastAPI TestClient fixture."""
    resp = client.get("/nrfi/today")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Phase 5 — /nrfi/dashboard aggregator
# ---------------------------------------------------------------------------


def test_nrfi_dashboard_returns_full_envelope():
    """The dashboard endpoint must always return the full envelope
    keys, even when every section degrades to its empty form."""
    from api.routers.nrfi import get_nrfi_dashboard
    out = get_nrfi_dashboard()
    assert isinstance(out, dict)
    assert set(out.keys()) >= {
        "date", "board", "ytd_ledger",
        "parlay_candidates", "parlay_ledger",
    }
    assert isinstance(out["board"], list)
    assert isinstance(out["ytd_ledger"], list)
    assert isinstance(out["parlay_candidates"], list)
    assert isinstance(out["parlay_ledger"], dict)


def test_nrfi_dashboard_with_explicit_date():
    from api.routers.nrfi import get_nrfi_dashboard
    out = get_nrfi_dashboard(date="2026-04-20")
    assert out["date"] == "2026-04-20"


def test_nrfi_dashboard_parlay_ledger_summary_zeros_when_empty():
    """No tickets → all-zero summary. Critical because the dashboard
    page expects numeric fields to always be present."""
    from api.routers.nrfi import get_nrfi_dashboard
    out = get_nrfi_dashboard()
    summary = out["parlay_ledger"]
    for k in ("recorded", "settled", "pending"):
        assert summary[k] == 0
    for k in ("units_returned", "total_stake", "roi_pct"):
        assert summary[k] == 0.0


def test_nrfi_dashboard_route_via_test_client(client):
    resp = client.get("/nrfi/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert "board" in body
    assert "ytd_ledger" in body
    assert "parlay_candidates" in body
    assert "parlay_ledger" in body


def test_nrfi_dashboard_route_with_date_query(client):
    resp = client.get("/nrfi/dashboard?date=2026-04-20")
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-04-20"


def test_api_main_mounts_nrfi_dashboard_route():
    if "api.main" in sys.modules:
        del sys.modules["api.main"]
    main = importlib.import_module("api.main")
    paths = {r.path for r in main.app.routes}
    assert "/nrfi/dashboard" in paths
