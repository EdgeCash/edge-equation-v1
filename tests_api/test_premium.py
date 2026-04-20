PREMIUM_REQUIRED_KEYS = {
    "selection", "market_type", "sport",
    "line_odds", "line_number",
    "fair_prob", "expected_value", "edge", "grade", "kelly",
    "realization", "game_id", "event_time",
    "p10", "p50", "p90", "mean", "notes",
}


def test_premium_picks_200(client):
    r = client.get("/premium/picks/today")
    assert r.status_code == 200


def test_premium_picks_schema(client):
    r = client.get("/premium/picks/today")
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0
    for pp in body:
        assert set(pp.keys()) == PREMIUM_REQUIRED_KEYS
        # Distribution fields must be present (p10/p50/p90/mean)
        assert pp["p10"] is not None
        assert pp["p50"] is not None
        assert pp["p90"] is not None
        assert pp["mean"] is not None


def test_premium_picks_deterministic(client):
    r1 = client.get("/premium/picks/today").json()
    r2 = client.get("/premium/picks/today").json()
    assert r1 == r2


def test_premium_cards_daily_200(client):
    r = client.get("/premium/cards/daily")
    assert r.status_code == 200


def test_premium_cards_daily_schema(client):
    r = client.get("/premium/cards/daily").json()
    assert r["card_type"] == "premium_daily_edge"
    assert r["headline"] == "Premium Daily Edge"
    assert r["subhead"] == "Full distributions and model notes."
    assert r["tagline"] == "Facts. Not Feelings."
    assert isinstance(r["picks"], list)
    assert len(r["picks"]) > 0


def test_premium_cards_daily_deterministic(client):
    r1 = client.get("/premium/cards/daily").json()
    r2 = client.get("/premium/cards/daily").json()
    # generated_at will be equal since we pinned datetime.now()
    assert r1 == r2
