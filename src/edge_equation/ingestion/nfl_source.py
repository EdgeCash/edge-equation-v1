"""NFL source."""
from datetime import datetime, timedelta
from decimal import Decimal


class NflSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 13, 0, 0)
        prefix = f"NFL-{run_datetime.date().isoformat()}"
        return [
            {"league": "NFL", "game_id": f"{prefix}-KC-BUF",
             "start_time": base.isoformat(), "home_team": "BUF", "away_team": "KC"},
            {"league": "NFL", "game_id": f"{prefix}-DAL-PHI",
             "start_time": (base + timedelta(hours=3, minutes=25)).isoformat(), "home_team": "PHI", "away_team": "DAL"},
            {"league": "NFL", "game_id": f"{prefix}-SF-GB",
             "start_time": (base + timedelta(hours=7, minutes=15)).isoformat(), "home_team": "GB", "away_team": "SF"},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]; away = g["away_team"]
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": -145,
                "meta": {
                    "inputs": {"strength_home": 1.28, "strength_away": 1.14, "home_adv": 0.115},
                    "universal_features": {"home_edge": 0.07},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("47.5"), "odds": -110,
                "meta": {
                    "inputs": {"off_env": 1.03, "def_env": 1.01, "pace": 1.00, "dixon_coles_adj": 0.00},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Passing_Yards",
                "selection": f"{home} QB Over 275.5",
                "line": Decimal("275.5"), "odds": -110,
                "meta": {
                    "inputs": {"rate": 312.4},
                    "universal_features": {"form_off": 0.11, "matchup_strength": 0.09},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Rushing_Yards",
                "selection": f"{away} RB Over 65.5",
                "line": Decimal("65.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 78.5},
                    "universal_features": {"form_off": -0.04, "matchup_strength": -0.06},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Receiving_Yards",
                "selection": f"{home} WR Over 80.5",
                "line": Decimal("80.5"), "odds": -110,
                "meta": {
                    "inputs": {"rate": 92.3},
                    "universal_features": {"form_off": 0.07},
                },
            })
        return markets
