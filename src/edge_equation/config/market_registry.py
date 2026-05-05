# -------------------------
# WNBA Markets
# -------------------------

WNBA_MARKETS = {
    "points": {"sport": "wnba", "type": "prop"},
    "rebounds": {"sport": "wnba", "type": "prop"},
    "assists": {"sport": "wnba", "type": "prop"},
    "pra": {"sport": "wnba", "type": "prop"},
    "3pm": {"sport": "wnba", "type": "prop"},
    "fullgame_total": {"sport": "wnba", "type": "game"},
    "fullgame_ml": {"sport": "wnba", "type": "game"},
}

MARKET_REGISTRY.update(WNBA_MARKETS)
