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
#   spread_line_weight      per-sport point-to-probability conversion factor
#                           used by the Spread / Run_Line / Puck_Line branch
#                           in ProbabilityCalculator. line_adj = line *
#                           spread_line_weight, so a higher weight means a
#                           larger probability shift per unit of line.
#                           Calibrated conservatively per sport -- low-scoring
#                           sports (NHL, MLB) need a higher weight because a
#                           1.5-unit line is a large fraction of typical
#                           score variance; high-scoring sports (NFL, NBA)
#                           need a lower weight. These seed values are
#                           expected to be re-tuned against shadow-phase
#                           realization data before any public posting.
#
# The Bradley-Terry team_strength builder is NOT implemented in this phase --
# these are knobs staged for the next review-gated change per the user's
# instructions. Downstream code should read them via SportConfig.get(sport, key,
# default).
SPORT_CONFIG = {
    "MLB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.86'),
        # Apr 26, 2026: lowered from 0.113 to 0.075 after the Apr 25
        # Premium Daily showed +1.5 run-line picks grading A+ purely
        # from line value even under heavy strength shrinkage. Public
        # MLB closing-line behavior (Pinnacle, Circa) prices the +1.5
        # run-line worth ~10-12pp of probability vs the moneyline,
        # which on a per-run basis is closer to 0.07-0.08. Going with
        # 0.075 as a conservative midpoint; will tune against settled
        # data once the calibration loop has > 50 settled run-line
        # picks across the post-shrinkage epoch.
        "spread_line_weight": Decimal('0.075'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.83'),
        "decay_lambda": Decimal('0.95'),
        "form_window_games": 15,
        "pitching_weight": Decimal('0.55'),
        "bullpen_weight": Decimal('0.20'),
        "home_adv": Decimal('0.115'),
        "strength_blend": {
            "pyth": Decimal('0.55'),
            "form": Decimal('0.20'),
            "elo": Decimal('0.15'),
            "pitching": Decimal('0.10'),
        },
    },
    "KBO": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "spread_line_weight": Decimal('0.113'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.83'),
        "decay_lambda": Decimal('0.95'),
        "form_window_games": 15,
        "pitching_weight": Decimal('0.55'),
        "bullpen_weight": Decimal('0.20'),
        "home_adv": Decimal('0.115'),
        "strength_blend": {
            "pyth": Decimal('0.55'),
            "form": Decimal('0.20'),
            "elo": Decimal('0.15'),
            "pitching": Decimal('0.10'),
        },
    },
    "NPB": {
        "markets": ["ML", "Run_Line", "Total", "HR", "K", "NRFI", "YRFI"],
        "league_baseline_total": Decimal('8.65'),
        "spread_line_weight": Decimal('0.113'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('1.83'),
        "decay_lambda": Decimal('0.95'),
        "form_window_games": 15,
        "pitching_weight": Decimal('0.55'),
        "bullpen_weight": Decimal('0.20'),
        "home_adv": Decimal('0.115'),
        "strength_blend": {
            "pyth": Decimal('0.55'),
            "form": Decimal('0.20'),
            "elo": Decimal('0.15'),
            "pitching": Decimal('0.10'),
        },
    },
    "NFL": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards", "Receiving_Yards"],
        "league_baseline_total": Decimal('47.5'),
        "spread_line_weight": Decimal('0.023'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('2.37'),
        "decay_lambda": Decimal('0.92'),
        "form_window_games": 5,
        "home_adv": Decimal('0.150'),
        "strength_blend": {
            "pyth": Decimal('0.40'),
            "form": Decimal('0.30'),
            "elo": Decimal('0.30'),
            "pitching": Decimal('0.00'),
        },
    },
    "NCAA_Football": {
        "markets": ["ML", "Spread", "Total", "Passing_Yards", "Rushing_Yards"],
        "league_baseline_total": Decimal('48.5'),
        "spread_line_weight": Decimal('0.023'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('2.37'),
        "decay_lambda": Decimal('0.92'),
        "form_window_games": 5,
        "home_adv": Decimal('0.160'),
        "strength_blend": {
            "pyth": Decimal('0.40'),
            "form": Decimal('0.30'),
            "elo": Decimal('0.30'),
            "pitching": Decimal('0.00'),
        },
    },
    "NCAA_Basketball": {
        "markets": ["ML", "Spread", "Total", "Points", "Rebounds", "Assists"],
        "league_baseline_total": Decimal('155.0'),
        "spread_line_weight": Decimal('0.015'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('11.5'),
        "decay_lambda": Decimal('0.93'),
        "form_window_games": 10,
        "home_adv": Decimal('0.170'),
        "strength_blend": {
            "pyth": Decimal('0.45'),
            "form": Decimal('0.25'),
            "elo": Decimal('0.30'),
            "pitching": Decimal('0.00'),
        },
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
        "strength_blend": {
            "pyth": Decimal('0.40'),
            "form": Decimal('0.30'),
            "elo": Decimal('0.30'),
            "pitching": Decimal('0.00'),
        },
    },
    "NHL": {
        "markets": ["ML", "Puck_Line", "Total", "SOG"],
        "league_baseline_total": Decimal('5.85'),
        "spread_line_weight": Decimal('0.133'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('2.15'),
        "decay_lambda": Decimal('0.94'),
        "form_window_games": 10,
        "home_adv": Decimal('0.100'),
        "strength_blend": {
            "pyth": Decimal('0.40'),
            "form": Decimal('0.30'),
            "elo": Decimal('0.30'),
            "pitching": Decimal('0.00'),
        },
    },
    # NBA: 82-game regular season with heavy travel; Pythagorean
    # exponent is high (~14) because NBA scoring is additive and
    # runaway wins dominate the strength signal. Shorter decay window
    # than NFL since pace / rotation changes move quickly. home_adv
    # matches the long-run NBA home-court effect (~60% win rate).
    "NBA": {
        "markets": ["ML", "Spread", "Total", "Points", "Rebounds", "Assists"],
        "league_baseline_total": Decimal('225.0'),
        "spread_line_weight": Decimal('0.015'),
        "ml_universal_weight": Decimal('0.65'),
        "prop_universal_weight": Decimal('0.55'),
        "pythagorean_exponent": Decimal('14.0'),
        "decay_lambda": Decimal('0.93'),
        "form_window_games": 10,
        "home_adv": Decimal('0.150'),
        "strength_blend": {
            "pyth": Decimal('0.40'),
            "form": Decimal('0.30'),
            "elo": Decimal('0.30'),
            "pitching": Decimal('0.00'),
        },
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

    @staticmethod
    def strength_blend(sport: str) -> dict:
        """Return the per-sport pyth/form/elo/pitching blend weights.

        Unknown sports get a neutral pyth/form/elo split instead of a
        KeyError crash. The caller (TeamStrengthBuilder) then produces
        a generic strength estimate; the engine still grades picks on
        whatever markets arrive, just without a sport-specific tuning
        advantage. This was the Phase 27b fix -- the backfill settler
        now pulls game data for any league with a sport_key mapping,
        so unmapped-to-config sports shouldn't crash the slate builder.
        """
        cfg = SPORT_CONFIG.get(sport)
        if cfg is None or "strength_blend" not in cfg:
            return {
                "pyth": Decimal('0.40'),
                "form": Decimal('0.30'),
                "elo": Decimal('0.30'),
                "pitching": Decimal('0.00'),
            }
        return dict(cfg["strength_blend"])
