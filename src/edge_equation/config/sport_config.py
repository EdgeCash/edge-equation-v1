from decimal import Decimal

SPORT_CONFIG = {
    "MLB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.86'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "KBO": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NPB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NFL": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards", "Receiving_Yards"],
        "league_baseline_total": Decimal('47.5'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NCAA_Football": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards"],
        "league_baseline_total": Decimal('48.5'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NCAA_Basketball": {
        "markets": ["ML", "Spread", "Total", "Points", "Rebounds", "Assists"],
        "league_baseline_total": Decimal('155.0'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "Soccer": {
        "markets": ["ML", "Total", "BTTS"],
        "league_baseline_total": Decimal('2.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
    "NHL": {
        "markets": ["ML", "Puck_Line", "Total", "SOG"],
        "league_baseline_total": Decimal('5.85'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
    },
}
