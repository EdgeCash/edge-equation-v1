"""NHL source."""
from datetime import datetime, timedelta
from decimal import Decimal


class NhlSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 19, 30, 0)
        prefix = f"NHL-{run_datetime.date().isoformat()}"
        return [
            {"league": "NHL", "game_id": f"{prefix}-PHI-PIT",
             "start_time": base.isoformat(), "home_team": "PIT", "away_team": "PHI"},
            {"league": "NHL", "game_id": f"{prefix}-BOS-TOR",
             "start_time": (base + timedelta(minutes=30)).isoformat(), "home_team": "TOR", "away_team": "BOS"},
            {"league": "NHL", "game_id": f"{prefix}-COL-VGK",
             "start_time": (base + timedelta(hours=2)).isoformat(), "home_team": "VGK", "away_team": "COL"},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]
            ml_inputs = {"strength_home": 1.22, "strength_away": 1.10, "home_adv": 0.095}
            ml_univ = {"home_edge": 0.05}
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": -125,
                "meta": {"inputs": ml_inputs, "universal_features": ml_univ},
            })
            markets.append({
                "game_id": gid, "market_type": "Puck_Line", "selection": f"{home} -1.5",
                "line": Decimal("-1.5"), "odds": +180,
                "meta": {"inputs": ml_inputs, "universal_features": ml_univ},
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("6.5"), "odds": -105,
                "meta": {
                    "inputs": {"off_env": 1.08, "def_env": 1.04, "pace": 1.02, "dixon_coles_adj": -0.05},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "SOG", "selection": f"{home} star Over 4.5 SOG",
                "line": Decimal("4.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 4.12},
                    "universal_features": {"matchup_exploit": 0.10},
                },
            })
        return markets
