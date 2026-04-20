"""MLB-like source: MLB, KBO, NPB."""
from datetime import datetime, timedelta
from decimal import Decimal


class MlbLikeSource:
    def __init__(self, league: str):
        if league not in ("MLB", "KBO", "NPB"):
            raise ValueError(f"MlbLikeSource supports MLB/KBO/NPB, got {league}")
        self.league = league

    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 13, 5, 0)
        prefix = f"{self.league}-{run_datetime.date().isoformat()}"
        if self.league == "MLB":
            games = [
                {"league": "MLB", "game_id": f"{prefix}-DET-BOS",
                 "start_time": base.isoformat(), "home_team": "BOS", "away_team": "DET"},
                {"league": "MLB", "game_id": f"{prefix}-HOU-CLE",
                 "start_time": (base + timedelta(hours=3)).isoformat(), "home_team": "CLE", "away_team": "HOU"},
                {"league": "MLB", "game_id": f"{prefix}-CIN-TB",
                 "start_time": (base + timedelta(hours=5)).isoformat(), "home_team": "TB", "away_team": "CIN"},
            ]
        elif self.league == "KBO":
            games = [
                {"league": "KBO", "game_id": f"{prefix}-LG-KIA",
                 "start_time": base.isoformat(), "home_team": "KIA", "away_team": "LG"},
                {"league": "KBO", "game_id": f"{prefix}-SSG-LT",
                 "start_time": (base + timedelta(hours=1)).isoformat(), "home_team": "LT", "away_team": "SSG"},
            ]
        else:
            games = [
                {"league": "NPB", "game_id": f"{prefix}-YKT-HNS",
                 "start_time": base.isoformat(), "home_team": "HNS", "away_team": "YKT"},
                {"league": "NPB", "game_id": f"{prefix}-RKT-SFB",
                 "start_time": (base + timedelta(hours=1)).isoformat(), "home_team": "SFB", "away_team": "RKT"},
            ]
        return games

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for idx, g in enumerate(games):
            gid = g["game_id"]; home = g["home_team"]; away = g["away_team"]
            strength_home = 1.32 if idx == 0 else (1.20 + 0.02 * idx)
            strength_away = 1.15 if idx == 0 else (1.10 + 0.01 * idx)
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home,
                "odds": -132 if idx == 0 else -115,
                "meta": {
                    "inputs": {"strength_home": strength_home, "strength_away": strength_away, "home_adv": 0.115},
                    "universal_features": {"home_edge": 0.085},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("9.5"), "odds": -110,
                "meta": {
                    "inputs": {"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "K", "selection": f"{home} SP Over 6.5 K",
                "line": Decimal("6.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 7.85},
                    "universal_features": {"matchup_exploit": 0.09, "market_line_delta": 0.08},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "HR", "selection": f"{away} batter Over 0.5 HR",
                "line": Decimal("0.5"), "odds": 320,
                "meta": {
                    "inputs": {"rate": 0.142},
                    "universal_features": {"matchup_exploit": 0.08, "market_line_delta": 0.12},
                },
            })
        return markets
