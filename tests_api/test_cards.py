REQUIRED_KEYS = {"card_type", "headline", "subhead", "picks", "tagline"}


def test_cards_daily_200(client):
    r = client.get("/cards/daily")
    assert r.status_code == 200


def test_cards_daily_schema(client):
    r = client.get("/cards/daily")
    body = r.json()
    for k in REQUIRED_KEYS:
        assert k in body, f"missing key: {k}"
    assert body["tagline"] == "Facts. Not Feelings."
    assert isinstance(body["picks"], list)


def test_cards_daily_card_type(client):
    r = client.get("/cards/daily")
    body = r.json()
    assert body["card_type"] == "daily_edge"


def test_cards_daily_deterministic(client):
    r1 = client.get("/cards/daily").json()
    r2 = client.get("/cards/daily").json()
    assert r1 == r2
