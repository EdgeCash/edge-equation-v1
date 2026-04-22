"""
Premium router gating tests.

Verify the PREMIUM_AUTH_REQUIRED env var flip turns the paywall on without
breaking the pre-Phase-17 default (open access).
"""
import pytest

from edge_equation.auth.sessions import COOKIE_NAME
from edge_equation.auth.subscriptions import SubscriptionStore
from edge_equation.auth.tokens import AuthTokenStore
from edge_equation.auth.users import UserStore
from edge_equation.persistence.db import Database


def _login(client, tmp_path, email="bob@example.com"):
    conn = Database.open(str(tmp_path / "p.db"))
    Database.migrate(conn)
    raw, _ = AuthTokenStore.mint(conn, email)
    conn.close()
    r = client.get("/auth/verify", params={"token": raw}, follow_redirects=False)
    return r.cookies[COOKIE_NAME]


@pytest.fixture
def gated(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "p.db"))
    monkeypatch.setenv("WEBSITE_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("EE_COOKIE_SECURE", "false")
    monkeypatch.setenv("PREMIUM_AUTH_REQUIRED", "true")


@pytest.fixture
def ungated(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "p.db"))
    monkeypatch.setenv("WEBSITE_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("EE_COOKIE_SECURE", "false")
    monkeypatch.delenv("PREMIUM_AUTH_REQUIRED", raising=False)


# ------------------------------------------------ ungated (default) path


def test_premium_picks_open_by_default(client, ungated):
    r = client.get("/premium/picks/today")
    assert r.status_code == 200


def test_premium_cards_open_by_default(client, ungated):
    r = client.get("/premium/cards/daily")
    assert r.status_code == 200


# ------------------------------------------------ gated path


def test_premium_picks_gated_unauthenticated_401(client, gated):
    r = client.get("/premium/picks/today")
    assert r.status_code == 401


def test_premium_picks_gated_authenticated_without_sub_403(client, gated, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    r = client.get("/premium/picks/today", cookies={COOKIE_NAME: session_id})
    assert r.status_code == 403
    assert "subscription" in r.json()["detail"].lower()


def test_premium_picks_gated_authenticated_with_sub_200(client, gated, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    conn = Database.open(str(tmp_path / "p.db"))
    user = UserStore.get_by_email(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, user.user_id, "sub_1", "active")
    conn.close()
    r = client.get("/premium/picks/today", cookies={COOKIE_NAME: session_id})
    assert r.status_code == 200


def test_premium_cards_gated_with_sub_200(client, gated, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    conn = Database.open(str(tmp_path / "p.db"))
    user = UserStore.get_by_email(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, user.user_id, "sub_1", "trialing")  # trialing entitles too
    conn.close()
    r = client.get("/premium/cards/daily", cookies={COOKIE_NAME: session_id})
    assert r.status_code == 200


def test_premium_gated_canceled_sub_403(client, gated, tmp_path):
    session_id = _login(client, tmp_path, email="bob@example.com")
    conn = Database.open(str(tmp_path / "p.db"))
    user = UserStore.get_by_email(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, user.user_id, "sub_1", "canceled")
    conn.close()
    r = client.get("/premium/picks/today", cookies={COOKIE_NAME: session_id})
    assert r.status_code == 403
