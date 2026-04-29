from decimal import Decimal


def test_normalize_prop_quotes_uses_preferred_bookmaker():
    from edge_equation.engines.props_prizepicks.source.odds_api import (
        normalize_prop_quotes,
    )

    payload = {
        "games": [{
            "id": "evt1",
            "commence_time": "2026-04-29T23:00:00Z",
            "home_team": "Boston Red Sox",
            "away_team": "New York Yankees",
            "bookmakers": [
                {"key": "draftkings", "markets": []},
                {
                    "key": "fanduel",
                    "markets": [{
                        "key": "batter_hits",
                        "outcomes": [{
                            "name": "Over",
                            "description": "Aaron Judge",
                            "point": 1.5,
                            "price": -115,
                        }],
                    }],
                },
            ],
        }],
    }

    quotes = normalize_prop_quotes(payload, preferred_bookmaker="fanduel")
    assert len(quotes) == 1
    assert quotes[0].player_name == "Aaron Judge"
    assert quotes[0].line == Decimal("1.5")
    assert quotes[0].bookmaker == "fanduel"


def test_prop_projection_and_output_classifies_edge():
    from edge_equation.engines.props_prizepicks.models import project_from_quote
    from edge_equation.engines.props_prizepicks.output import (
        build_prop_output,
        render_prop_output,
    )
    from edge_equation.engines.props_prizepicks.source.odds_api import PropMarketQuote
    from edge_equation.engines.tiering import Tier

    quote = PropMarketQuote(
        event_id="evt1",
        commence_time="2026-04-29T23:00:00Z",
        home_team="Boston Red Sox",
        away_team="New York Yankees",
        bookmaker="draftkings",
        market_key="batter_hits",
        player_name="Aaron Judge",
        side="Over",
        line=Decimal("1.5"),
        american_odds=-110,
    )
    projection = project_from_quote(quote, model_prob=0.61, vig_buffer=0.02)
    out = build_prop_output(projection)

    assert out.tier == Tier.LOCK
    rendered = render_prop_output(out)
    assert "Aaron Judge" in rendered
    assert "edge=+8.6pp" in rendered
