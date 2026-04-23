"""NBA source (uses NCAA_Basketball config for math)."""
from datetime import datetime, timedelta
from decimal import Decimal


class NbaSource:
    def get_raw_games(self, run_datetime: datetime) -> list:
        base = datetime(run_datetime.year, run_datetime.month, run_datetime.day, 19, 0, 0)
        prefix = f"NBA-{run_datetime.date().isoformat()}"
        return [
            {"league": "NBA", "game_id": f"{prefix}-LAL-BOS",
             "start_time": base.isoformat(), "home_team": "BOS", "away_team": "LAL"},
            {"league": "NBA", "game_id": f"{prefix}-GSW-MIL",
             "start_time": (base + timedelta(hours=1)).isoformat(), "home_team": "MIL", "away_team": "GSW"},
            {"league": "NBA", "game_id": f"{prefix}-DEN-PHX",
             "start_time": (base + timedelta(hours=3)).isoformat(), "home_team": "PHX", "away_team": "DEN"},
        ]

    def get_raw_markets(self, run_datetime: datetime) -> list:
        games = self.get_raw_games(run_datetime)
        markets = []
        for g in games:
            gid = g["game_id"]; home = g["home_team"]; away = g["away_team"]
            markets.append({
                "game_id": gid, "market_type": "ML", "selection": home, "odds": -140,
                "meta": {
                    "inputs": {"strength_home": 1.30, "strength_away": 1.12, "home_adv": 0.115},
                    "universal_features": {"home_edge": 0.060, "form_off": 0.02},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Total", "selection": "Over",
                "line": Decimal("225.5"), "odds": -110,
                "meta": {
                    "inputs": {"off_env": 1.05, "def_env": 1.02, "pace": 1.01, "dixon_coles_adj": 0.00},
                    "universal_features": {},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Points", "selection": f"{home} star Over 26.5",
                "line": Decimal("26.5"), "odds": -115,
                "meta": {
                    "inputs": {"rate": 28.3},
                    "universal_features": {"matchup_exploit": 0.05, "form_off": 0.04},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Rebounds", "selection": f"{away} big Over 8.5",
                "line": Decimal("8.5"), "odds": -120,
                "meta": {
                    "inputs": {"rate": 9.2},
                    "universal_features": {"form_def": -0.03},
                },
            })
            markets.append({
                "game_id": gid, "market_type": "Assists", "selection": f"{home} guard Over 6.5",
                "line": Decimal("6.5"), "odds": -110,
                "meta": {
                    "inputs": {"rate": 7.1},
                    "universal_features": {"form_off": 0.02},
                },
            })
            # Spread emitted as two outcomes: home -5.5 and away +5.5.
            # Selections are bare team names; the line lives on
            # MarketInfo.line so _resolve_selection_side can exact-match.
            sp_inputs = {"strength_home": 1.30, "strength_away": 1.12, "home_adv": 0.115}
            sp_univ = {"home_edge": 0.060, "form_off": 0.02}
            markets.append({
                "game_id": gid, "market_type": "Spread", "selection": home,
                "line": Decimal("-5.5"), "odds": -110,
                "meta": {"inputs": sp_inputs, "universal_features": sp_univ},
            })
            markets.append({
                "game_id": gid, "market_type": "Spread", "selection": away,
                "line": Decimal("5.5"), "odds": -110,
                "meta": {"inputs": sp_inputs, "universal_features": sp_univ},
            })
        return markets
