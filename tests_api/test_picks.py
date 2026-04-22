REQUIRED_KEYS = {
    "selection", "market_type", "sport",
    "line_odds", "line_number",
    "fair_prob", "expected_value", "edge", "grade", "kelly",
    "realization", "game_id", "event_time",
}


def test_picks_today_200(client):
    r = client.get("/picks/today")
    assert r.status_code == 200


def test_picks_today_returns_list(client):
    r = client.get("/picks/today")
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0


def test_picks_today_schema(client):
    r = client.get("/picks/today")
    body = r.json()
    for pick in body:
        assert set(pick.keys()) == REQUIRED_KEYS
        assert isinstance(pick["selection"], str)
        assert isinstance(pick["market_type"], str)
        assert isinstance(pick["grade"], str)
        assert isinstance(pick["line_odds"], int)
        assert isinstance(pick["realization"], int)


def test_picks_today_deterministic(client):
    r1 = client.get("/picks/today").json()
    r2 = client.get("/picks/today").json()
    assert r1 == r2


def test_picks_contains_known_det_at_bos_ml(client):
    r = client.get("/picks/today").json()
    # The DET @ BOS ML is our canonical reference pick.
    ml = [p for p in r if p["market_type"] == "ML" and p["selection"] == "BOS"
          and p["sport"] == "MLB"]
    assert ml, "Expected to find BOS ML in MLB picks"
    pick = ml[0]
    assert pick["fair_prob"] == "0.618133"
    assert pick["edge"] == "0.049167"
    # Phase 18 tightened grade thresholds: 0.049167 is in the B tier
    # (>=0.03 but <0.05). Under the pre-Phase-18 ladder this was an "A".
    assert pick["grade"] == "B"
    assert pick["kelly"] == "0.0324"
    # Realization bucket for B under Phase 18 is 52 (was A/59 pre-Phase-18).
    assert pick["realization"] == 52
