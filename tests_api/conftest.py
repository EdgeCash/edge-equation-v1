"""
Shared API test fixtures.

Uses pinned datetime (2026-04-20T09:00:00) so tests are independent of
wall-clock time. The api.data_source module is monkeypatched to use
this pinned datetime instead of datetime.now().
"""
import pytest
from datetime import datetime

from fastapi.testclient import TestClient

from api.main import app
from api import data_source as _ds


PINNED_RUN_DT = datetime(2026, 4, 20, 9, 0, 0)


@pytest.fixture(autouse=True)
def pin_clock(monkeypatch):
    """Monkeypatch datetime.now() usage inside data_source and routers."""
    # Wrap the existing helpers so any routers or code relying on them
    # see a deterministic clock.
    real_picks_for_today = _ds.picks_for_today
    real_premium_picks_for_today = _ds.premium_picks_for_today
    real_slate_entries_for_sport = _ds.slate_entries_for_sport

    def pinned_picks(run_datetime=None):
        return real_picks_for_today(run_datetime or PINNED_RUN_DT)

    def pinned_premium(run_datetime=None, seed=42, iterations=1000):
        return real_premium_picks_for_today(run_datetime or PINNED_RUN_DT, seed, iterations)

    def pinned_slate_entries(sport, run_datetime=None):
        return real_slate_entries_for_sport(sport, run_datetime or PINNED_RUN_DT)

    monkeypatch.setattr(_ds, "picks_for_today", pinned_picks)
    monkeypatch.setattr(_ds, "premium_picks_for_today", pinned_premium)
    monkeypatch.setattr(_ds, "slate_entries_for_sport", pinned_slate_entries)

    # Patch names imported into router modules
    import api.routers.picks as _r_picks
    import api.routers.premium as _r_premium
    import api.routers.slate as _r_slate
    monkeypatch.setattr(_r_picks, "picks_for_today", pinned_picks)
    monkeypatch.setattr(_r_premium, "premium_picks_for_today", pinned_premium)
    monkeypatch.setattr(_r_slate, "slate_entries_for_sport", pinned_slate_entries)

    # Pin datetime.now() used by cards.py and premium.py card endpoint
    import api.routers.cards as _r_cards
    import api.routers.premium as _r_premium2

    class _PinnedDatetime:
        @staticmethod
        def now():
            return PINNED_RUN_DT

    monkeypatch.setattr(_r_cards, "datetime", _PinnedDatetime)
    monkeypatch.setattr(_r_premium2, "datetime", _PinnedDatetime)


@pytest.fixture
def client():
    return TestClient(app)
