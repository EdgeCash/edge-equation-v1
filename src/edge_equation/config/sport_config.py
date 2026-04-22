from decimal import Decimal


# Phase 18 adds per-sport tuning knobs for the Bradley-Terry / Pythagorean
# team-strength builder (still deterministic, no ML):
#
#   pythagorean_exponent    runs/points exponent in P = RS^x / (RS^x + RA^x).
#                           MLB ~1.83 (Bill James); NFL ~2.37; NBA ~13.91
#                           (Oliver); NHL ~2.15; Soccer ~1.3.
#   decay_lambda            exponential recency weight lambda in [0, 1] for
#                           blending recent-form into team strength.
#   form_window_games       how many recent games feed the recency blend.
#   pitching_weight         baseball-family only: share of team strength
#                           attributable to pitching (FIP/xFIP + bullpen).
#   bullpen_weight          baseball-family only: sub-share of pitching_weight
#                           attributable to relief pitching.
#   home_adv                default Bradley-Terry home exponent (same units
#                           as the existing `home_adv` input in probability.py).
#
# The Bradley-Terry team_strength builder is NOT implemented in this phase --
# these are knobs staged for the next review-gated change per the user's
# instructions. Downstream code should read them via SportConfig.get(sport, key,
# default).
SPORT_CONFIG = {
    "MLB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.86'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.83'),
        "decay_lambda": Decimal('0.95'),
        "form_window_games": 15,
        "pitching_weight": Decimal('0.55'),
        "bullpen_weight": Decimal('0.20'),
        "home_adv": Decimal('0.115'),
    },
    "KBO": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.83'),
        "decay_lambda": Decimal('0.95'),
        "form_window_games": 15,
        "pitching_weight": Decimal('0.55'),
        "bullpen_weight": Decimal('0.20'),
        "home_adv": Decimal('0.115'),
    },
    "NPB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.83'),
        "decay_lambda": Decimal('0.95'),
        "form_window_games": 15,
        "pitching_weight": Decimal('0.55'),
        "bullpen_weight": Decimal('0.20'),
        "home_adv": Decimal('0.115'),
    },
    "NFL": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards", "Receiving_Yards"],
        "league_baseline_total": Decimal('47.5'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('2.37'),
        "decay_lambda": Decimal('0.92'),
        "form_window_games": 5,
        "home_adv": Decimal('0.150'),
    },
    "NCAA_Football": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards"],
        "league_baseline_total": Decimal('48.5'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('2.37'),
        "decay_lambda": Decimal('0.92'),
        "form_window_games": 5,
        "home_adv": Decimal('0.160'),
    },
    "NCAA_Basketball": {
        "markets": ["ML", "Spread", "Total", "Points", "Rebounds", "Assists"],
        "league_baseline_total": Decimal('155.0'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('11.5'),
        "decay_lambda": Decimal('0.93'),
        "form_window_games": 10,
        "home_adv": Decimal('0.170'),
    },
    "Soccer": {
        "markets": ["ML", "Total", "BTTS"],
        "league_baseline_total": Decimal('2.65'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.3'),
        "decay_lambda": Decimal('0.94'),
        "form_window_games": 10,
        "home_adv": Decimal('0.130'),
    },
    "NHL": {
        "markets": ["ML", "Puck_Line", "Total", "SOG"],
        "league_baseline_total": Decimal('5.85'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('2.15'),
        "decay_lambda": Decimal('0.94'),
        "form_window_games": 10,
        "home_adv": Decimal('0.100'),
    },
}


class SportConfig:
    """
    Safe accessors:
    - get(sport, key, default=None) -> value or default
    - require(sport, key)           -> value or raise
    - pythagorean_exponent(sport)   -> Decimal
    - decay_lambda(sport)           -> Decimal
    - form_window_games(sport)      -> int
    - pitching_weight(sport)        -> Decimal | None
    - bullpen_weight(sport)         -> Decimal | None
    - home_adv(sport)               -> Decimal
    """

    @staticmethod
    def get(sport: str, key: str, default=None):
        cfg = SPORT_CONFIG.get(sport)
        if cfg is None:
            return default
        return cfg.get(key, default)

    @staticmethod
    def require(sport: str, key: str):
        cfg = SPORT_CONFIG.get(sport)
        if cfg is None:
            raise KeyError(f"Unknown sport: {sport!r}")
        if key not in cfg:
            raise KeyError(f"Sport {sport!r} has no {key!r}")
        return cfg[key]

    @staticmethod
    def pythagorean_exponent(sport: str):
        return SportConfig.require(sport, "pythagorean_exponent")

    @staticmethod
    def decay_lambda(sport: str):
        return SportConfig.require(sport, "decay_lambda")

    @staticmethod
    def form_window_games(sport: str) -> int:
        return int(SportConfig.require(sport, "form_window_games"))

    @staticmethod
    def pitching_weight(sport: str):
        return SportConfig.get(sport, "pitching_weight", None)

    @staticmethod
    def bullpen_weight(sport: str):
        return SportConfig.get(sport, "bullpen_weight", None)

    @staticmethod
    def home_adv(sport: str):
        return SportConfig.require(sport, "home_adv")
