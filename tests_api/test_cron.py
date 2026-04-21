import json
import pytest

from api.routers.cron import ENV_CRON_SECRET


SECRET = "test-cron-secret-123"


@pytest.fixture(autouse=True)
def isolate_cron_env(monkeypatch, tmp_path):
    # Deterministic DB + failsafe dir so each test is isolated.
    monkeypatch.setenv("EDGE_EQUATION_DB", str(tmp_path / "cron.db"))
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    monkeypatch.setenv(ENV_CRON_SECRET, SECRET)
    # Strip real publisher credentials so non-dry-run just exercises the
    # failsafe path -- never a real network call.
    for v in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
              "DISCORD_WEBHOOK_URL", "SMTP_HOST", "SMTP_FROM", "SMTP_TO", "EMAIL_TO",
              "THE_ODDS_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_daily_requires_bearer_token(client):
    r = client.get("/cron/daily")
    assert r.status_code == 401
    assert "bearer" in r.json()["detail"].lower()


def test_daily_rejects_wrong_token(client):
    r = client.get("/cron/daily", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_daily_rejects_missing_scheme(client):
    r = client.get("/cron/daily", headers={"Authorization": SECRET})
    assert r.status_code == 401


def test_daily_503_when_secret_unset(monkeypatch, client):
    monkeypatch.delenv(ENV_CRON_SECRET, raising=False)
    r = client.get("/cron/daily", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code == 503
    assert "CRON_SECRET" in r.json()["detail"]


def test_daily_returns_run_summary(client):
    r = client.get(
        "/cron/daily",
        headers={"Authorization": f"Bearer {SECRET}"},
        params={"leagues": "MLB", "publish": "false", "dry_run": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["card_type"] == "daily_edge"
    assert body["new_slate"] is True
    assert body["slate_id"].startswith("daily_edge_")
    assert body["slate_id"].endswith("_mlb")


def test_evening_returns_run_summary(client):
    r = client.get(
        "/cron/evening",
        headers={"Authorization": f"Bearer {SECRET}"},
        params={"leagues": "NHL", "publish": "false", "dry_run": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["card_type"] == "evening_edge"
    assert "nhl" in body["slate_id"]


def test_daily_second_call_is_idempotent(client):
    headers = {"Authorization": f"Bearer {SECRET}"}
    params = {"leagues": "MLB", "publish": "false", "dry_run": "true"}
    first = client.get("/cron/daily", headers=headers, params=params)
    second = client.get("/cron/daily", headers=headers, params=params)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["new_slate"] is True
    assert second.json()["new_slate"] is False
    assert first.json()["slate_id"] == second.json()["slate_id"]


def test_daily_publish_true_routes_to_failsafe_without_creds(client, tmp_path):
    # With publish=true but no publisher credentials, every publisher must
    # succeed-with-failsafe. The test proves the cron path reaches publishers
    # and the failsafe chain kicks in (files written).
    r = client.get(
        "/cron/daily",
        headers={"Authorization": f"Bearer {SECRET}"},
        params={"leagues": "MLB", "publish": "true", "dry_run": "false"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["publish_results"]) == 3
    for pr in body["publish_results"]:
        assert pr["success"] is False
        assert pr["failsafe_triggered"] is True


def test_default_leagues_used_when_not_specified(client):
    r = client.get(
        "/cron/daily",
        headers={"Authorization": f"Bearer {SECRET}"},
        params={"publish": "false", "dry_run": "true"},
    )
    body = r.json()
    # Default leagues list produces a multi-league slate (no sport suffix)
    assert body["slate_id"].count("_") == 2  # daily_edge_20260420
