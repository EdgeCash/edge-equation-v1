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
