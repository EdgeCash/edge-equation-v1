REQUIRED_KEYS = {
    "game_id", "home_team", "away_team",
    "moneyline_home", "moneyline_away", "total", "event_time",
}


def test_slate_mlb_200(client):
    r = client.get("/slate/mlb")
    assert r.status_code == 200


def test_slate_mlb_returns_list(client):
    r = client.get("/slate/mlb").json()
    assert isinstance(r, list)
    assert len(r) > 0


def test_slate_mlb_schema(client):
    r = client.get("/slate/mlb").json()
    for entry in r:
        assert set(entry.keys()) == REQUIRED_KEYS
        assert isinstance(entry["game_id"], str)
        assert isinstance(entry["home_team"], str)
        assert isinstance(entry["away_team"], str)


def test_slate_nba_200(client):
    r = client.get("/slate/nba")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_slate_nhl_200(client):
    r = client.get("/slate/nhl")
    assert r.status_code == 200


def test_slate_uppercase_sport(client):
    r = client.get("/slate/MLB")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_slate_soccer_alias(client):
    r = client.get("/slate/soccer")
    assert r.status_code == 200


def test_slate_unknown_sport_404(client):
    r = client.get("/slate/cricket")
    assert r.status_code == 404


def test_slate_deterministic(client):
    r1 = client.get("/slate/mlb").json()
    r2 = client.get("/slate/mlb").json()
    assert r1 == r2


def test_slate_first_mlb_entry_matches_source(client):
    r = client.get("/slate/mlb").json()
    first = r[0]
    assert first["game_id"] == "MLB-2026-04-20-DET-BOS"
    assert first["home_team"] == "BOS"
    assert first["away_team"] == "DET"
    assert first["moneyline_home"] == -132
    assert first["total"] == "9.5"
