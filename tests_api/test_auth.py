"""Auth router tests."""
import hmac
import hashlib
import time
from unittest.mock import patch

import httpx
import pytest

from edge_equation.auth.sessions import COOKIE_NAME, SessionStore
from edge_equation.auth.subscriptions import SubscriptionStore
from edge_equation.auth.tokens import AuthTokenStore, _hash_token
from edge_equation.auth.users import UserStore
from edge_equation.persistence.db import Database


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    monkeypatch.setenv("WEBSITE_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("EE_COOKIE_SECURE", "false")
    # No SMTP set up -> MagicLinkSender raises RuntimeError -> router
    # silently succeeds (never leaks SMTP config state to callers).
    for v in ("SMTP_HOST", "SMTP_FROM", "SMTP_TO", "EMAIL_TO"):
        monkeypatch.delenv(v, raising=False)


# ----------------------------------------------------------- request-link


def test_request_link_202_always(client):
    r = client.post("/auth/request-link", json={"email": "bob@example.com"})
    assert r.status_code == 202
    # Never reveals whether the email exists
    assert r.json()["status"] == "ok"


def test_request_link_bad_email_rejected(client):
    r = client.post("/auth/request-link", json={"email": "not-an-email"})
    assert r.status_code == 422  # pydantic validation


def test_request_link_creates_auth_token_row(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "auth2.db"))
    r = client.post("/auth/request-link", json={"email": "bob@example.com"})
    assert r.status_code == 202
    conn = Database.open(str(tmp_path / "auth2.db"))
    rows = conn.execute("SELECT COUNT(*) AS c FROM auth_tokens").fetchone()
    assert int(rows["c"]) == 1
    conn.close()


# ----------------------------------------------------------- verify


def test_verify_missing_token_rejected(client):
    r = client.get("/auth/verify", follow_redirects=False)
    assert r.status_code == 400


def test_verify_bad_token_rejected(client):
    r = client.get("/auth/verify", params={"token": "garbage"}, follow_redirects=False)
    assert r.status_code == 400


def test_verify_valid_token_redirects_and_sets_cookie(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "auth.db"))
    conn = Database.open(str(tmp_path / "auth.db"))
    Database.migrate(conn)
    raw, _ = AuthTokenStore.mint(conn, "bob@example.com")
    conn.close()

    r = client.get("/auth/verify", params={"token": raw}, follow_redirects=False)
    assert r.status_code == 302
    assert "/account" in r.headers["location"]
    assert COOKIE_NAME in r.cookies


def test_verify_second_consume_fails(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "auth.db"))
    conn = Database.open(str(tmp_path / "auth.db"))
    Database.migrate(conn)
    raw, _ = AuthTokenStore.mint(conn, "bob@example.com")
    conn.close()

    r1 = client.get("/auth/verify", params={"token": raw}, follow_redirects=False)
    assert r1.status_code == 302
    r2 = client.get("/auth/verify", params={"token": raw}, follow_redirects=False)
    assert r2.status_code == 400


def test_verify_creates_user_row(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "auth.db"))
    conn = Database.open(str(tmp_path / "auth.db"))
    Database.migrate(conn)
    raw, _ = AuthTokenStore.mint(conn, "newuser@example.com")
    conn.close()

    client.get("/auth/verify", params={"token": raw}, follow_redirects=False)

    conn = Database.open(str(tmp_path / "auth.db"))
    u = UserStore.get_by_email(conn, "newuser@example.com")
    assert u is not None
    assert u.email_verified_at is not None
    conn.close()


# ----------------------------------------------------------- me / logout


def test_me_unauthenticated_401(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


def _login(client, tmp_path, monkeypatch, email="bob@example.com"):
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "auth.db"))
    conn = Database.open(str(tmp_path / "auth.db"))
    Database.migrate(conn)
    raw, _ = AuthTokenStore.mint(conn, email)
    conn.close()
    r = client.get("/auth/verify", params={"token": raw}, follow_redirects=False)
    assert r.status_code == 302
    return r.cookies[COOKIE_NAME]


def test_me_authenticated_returns_user(client, tmp_path, monkeypatch):
    session_id = _login(client, tmp_path, monkeypatch, email="bob@example.com")
    r = client.get("/auth/me", cookies={COOKIE_NAME: session_id})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "bob@example.com"
    assert body["has_active_subscription"] is False
    assert body["subscription"] is None


def test_me_reflects_active_subscription(client, tmp_path, monkeypatch):
    session_id = _login(client, tmp_path, monkeypatch, email="sub@example.com")
    conn = Database.open(str(tmp_path / "auth.db"))
    user = UserStore.get_by_email(conn, "sub@example.com")
    SubscriptionStore.upsert(conn, user.user_id, "sub_X", "active")
    conn.close()

    r = client.get("/auth/me", cookies={COOKIE_NAME: session_id})
    assert r.json()["has_active_subscription"] is True
    assert r.json()["subscription"]["status"] == "active"


def test_logout_revokes_session(client, tmp_path, monkeypatch):
    session_id = _login(client, tmp_path, monkeypatch, email="bob@example.com")
    r = client.post("/auth/logout", cookies={COOKIE_NAME: session_id})
    assert r.status_code == 200
    # /auth/me should now 401 with the old cookie
    r2 = client.get("/auth/me", cookies={COOKIE_NAME: session_id})
    assert r2.status_code == 401


def test_logout_without_cookie_still_200(client):
    r = client.post("/auth/logout")
    assert r.status_code == 200


def test_invalid_cookie_value_returns_401(client):
    r = client.get("/auth/me", cookies={COOKIE_NAME: "not-a-real-session"})
    assert r.status_code == 401
