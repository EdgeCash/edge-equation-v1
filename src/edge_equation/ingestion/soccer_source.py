"""Soccer source."""
from datetime import datetime, timedelta
from decimal import Decimal


class SoccerSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 12, 30, 0)
        prefix = f"SOC-{run_datetime.date().isoformat()}"
        return [
            {"league": "SOC", "game_id": f"{prefix}-MCI-ARS",
             "start_time": base.isoformat(), "home_team": "ARS", "away_team": "MCI",
             "meta": {"competition": "EPL"}},
            {"league": "SOC", "game_id": f"{prefix}-RMA-BAR",
             "start_time": (base + timedelta(hours=3)).isoformat(), "home_team": "BAR", "away_team": "RMA",
             "meta": {"competition": "LaLiga"}},
            {"league": "SOC", "game_id": f"{prefix}-BAY-DOR",
             "start_time": (base + timedelta(hours=5)).isoformat(), "home_team": "DOR", "away_team": "BAY",
             "meta": {"competition": "Bundesliga"}},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": +120,
                "meta": {
                    "inputs": {"strength_home": 1.20, "strength_away": 1.15, "home_adv": 0.10},
                    "universal_features": {"home_edge": 0.06},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("2.5"), "odds": -105,
                "meta": {
                    "inputs": {"off_env": 1.02, "def_env": 1.01, "pace": 1.00, "dixon_coles_adj": -0.03},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "BTTS", "selection": "Yes", "odds": -130,
                "meta": {
                    "inputs": {"home_lambda": 1.35, "away_lambda": 1.20},
                    "universal_features": {"form_off": 0.03},
                },
            })
        return markets
