"""All 30 MLB ballparks: coordinates, altitude, dimensions, run/HR factors.

Park factors are 3-yr trailing run-environment indices normalised to
100 = league average. Source: Baseball Savant / Statcast park factors
(2022-2024 trailing window). Update annually if you want bleeding-edge
accuracy — they shift slowly so a yearly refresh is plenty.

Lat/long are used for the Open-Meteo weather lookup. Altitude is
included separately because it has a measurable effect on HR carry
distance independent of temperature/density (Coors is the obvious one).

`is_dome` = True for fixed-roof venues — when True, weather features
should be neutralised in `nrfi.features.feature_engineering`.

`is_retractable` = True for retractable-roof parks; we don't try to
predict roof state, so we treat them as 50/50 unless an explicit roof
status is passed in from the boxscore feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ParkInfo:
    code: str           # MLB Stats venue ID-friendly short code
    name: str
    team: str
    city: str
    lat: float
    lon: float
    altitude_ft: int
    lf_ft: int          # LF foul-pole distance
    cf_ft: int          # CF distance
    rf_ft: int          # RF foul-pole distance
    park_factor_runs: float    # 100 = neutral
    park_factor_hr: float
    park_factor_1st_inn: float # First-inning specific multiplier
    is_dome: bool = False
    is_retractable: bool = False
    orientation_deg: int = 0   # CF bearing from home plate (0=N, 90=E, ...)


# Keys are MLB team tricodes (ARI, ATL, ...). 1st-inning factors are
# centred at 1.00 — derive from your own backtests if you want to
# improve them.
PARKS: Mapping[str, ParkInfo] = {
    "ARI": ParkInfo("ARI", "Chase Field",         "Diamondbacks", "Phoenix",     33.4455, -112.0667, 1086, 330, 407, 335,  103,  108, 1.02, is_retractable=True,  orientation_deg= 23),
    "ATL": ParkInfo("ATL", "Truist Park",         "Braves",       "Atlanta",     33.8908,  -84.4678, 1050, 335, 400, 325,  101,  103, 1.01, orientation_deg=137),
    "BAL": ParkInfo("BAL", "Camden Yards",        "Orioles",      "Baltimore",   39.2839,  -76.6217,   20, 333, 410, 318,  104,  106, 1.03, orientation_deg= 32),
    "BOS": ParkInfo("BOS", "Fenway Park",         "Red Sox",      "Boston",      42.3467,  -71.0972,   21, 310, 390, 302,  108,   95, 1.05, orientation_deg= 45),
    "CHC": ParkInfo("CHC", "Wrigley Field",       "Cubs",         "Chicago",     41.9484,  -87.6553,  600, 355, 400, 353,  104,  104, 1.02, orientation_deg= 32),
    "CWS": ParkInfo("CWS", "Guaranteed Rate Fld", "White Sox",    "Chicago",     41.8300,  -87.6338,  595, 330, 400, 335,  101,  108, 1.01, orientation_deg= 41),
    "CIN": ParkInfo("CIN", "Great American Ball", "Reds",         "Cincinnati",  39.0975,  -84.5067,  480, 328, 404, 325,  103,  113, 1.02, orientation_deg= 35),
    "CLE": ParkInfo("CLE", "Progressive Field",   "Guardians",    "Cleveland",   41.4962,  -81.6852,  650, 325, 400, 325,   97,   98, 0.99, orientation_deg= 37),
    "COL": ParkInfo("COL", "Coors Field",         "Rockies",      "Denver",      39.7559, -104.9942, 5197, 347, 415, 350,  113,  115, 1.10, orientation_deg= 24),
    "DET": ParkInfo("DET", "Comerica Park",       "Tigers",       "Detroit",     42.3390,  -83.0485,  600, 345, 420, 330,   95,   91, 0.97, orientation_deg=128),
    "HOU": ParkInfo("HOU", "Minute Maid Park",    "Astros",       "Houston",     29.7572,  -95.3553,   22, 315, 409, 326,  100,  104, 1.00, is_retractable=True,  orientation_deg=345),
    "KC":  ParkInfo("KC",  "Kauffman Stadium",    "Royals",       "Kansas City", 39.0517,  -94.4803,  750, 330, 410, 330,   97,   91, 0.98, orientation_deg= 45),
    "LAA": ParkInfo("LAA", "Angel Stadium",       "Angels",       "Anaheim",     33.8003, -117.8827,  150, 333, 396, 333,   97,   98, 0.99, orientation_deg= 50),
    "LAD": ParkInfo("LAD", "Dodger Stadium",      "Dodgers",      "Los Angeles", 34.0739, -118.2400,  500, 330, 395, 330,   97,  104, 0.97, orientation_deg= 25),
    "MIA": ParkInfo("MIA", "loanDepot park",      "Marlins",      "Miami",       25.7781,  -80.2196,    8, 344, 407, 335,   90,   86, 0.95, is_retractable=True,  orientation_deg= 40),
    "MIL": ParkInfo("MIL", "American Family Fld", "Brewers",      "Milwaukee",   43.0280,  -87.9712,  635, 344, 400, 345,  102,  104, 1.01, is_retractable=True,  orientation_deg=132),
    "MIN": ParkInfo("MIN", "Target Field",        "Twins",        "Minneapolis", 44.9817,  -93.2789,  815, 339, 404, 328,   99,   97, 0.99, orientation_deg= 90),
    "NYM": ParkInfo("NYM", "Citi Field",          "Mets",         "New York",    40.7571,  -73.8458,   37, 335, 408, 330,   95,   91, 0.97, orientation_deg= 25),
    "NYY": ParkInfo("NYY", "Yankee Stadium",      "Yankees",      "New York",    40.8296,  -73.9262,   54, 318, 408, 314,  104,  117, 1.04, orientation_deg= 75),
    "OAK": ParkInfo("OAK", "Oakland Coliseum",    "Athletics",    "Oakland",     37.7516, -122.2008,   13, 330, 400, 330,   91,   88, 0.94, orientation_deg= 55),
    "PHI": ParkInfo("PHI", "Citizens Bank Park",  "Phillies",     "Philadelphia",39.9061,  -75.1665,   20, 329, 401, 330,  101,  108, 1.01, orientation_deg= 23),
    "PIT": ParkInfo("PIT", "PNC Park",            "Pirates",      "Pittsburgh",  40.4469,  -80.0057,  730, 325, 399, 320,   97,   91, 0.98, orientation_deg=120),
    "SD":  ParkInfo("SD",  "Petco Park",          "Padres",       "San Diego",   32.7073, -117.1566,   15, 336, 396, 322,   95,   92, 0.96, orientation_deg=  0),
    "SF":  ParkInfo("SF",  "Oracle Park",         "Giants",       "San Francisco",37.7786, -122.3893,   12, 339, 391, 309,   92,   86, 0.95, orientation_deg= 92),
    "SEA": ParkInfo("SEA", "T-Mobile Park",       "Mariners",     "Seattle",     47.5914, -122.3325,   17, 331, 401, 326,   94,   91, 0.96, is_retractable=True,  orientation_deg=  0),
    "STL": ParkInfo("STL", "Busch Stadium",       "Cardinals",    "St. Louis",   38.6226,  -90.1928,  465, 336, 400, 335,   98,   93, 0.99, orientation_deg= 56),
    "TB":  ParkInfo("TB",  "Tropicana Field",     "Rays",         "St. Petersburg",27.7682,-82.6534,   42, 315, 404, 322,   96,   93, 0.97, is_dome=True,        orientation_deg= 45),
    "TEX": ParkInfo("TEX", "Globe Life Field",    "Rangers",      "Arlington",   32.7473,  -97.0837,  551, 329, 407, 326,   99,  101, 1.00, is_retractable=True,  orientation_deg=  0),
    "TOR": ParkInfo("TOR", "Rogers Centre",       "Blue Jays",    "Toronto",     43.6414,  -79.3894,  300, 328, 400, 328,  102,  104, 1.01, is_retractable=True,  orientation_deg=  0),
    "WSH": ParkInfo("WSH", "Nationals Park",      "Nationals",    "Washington",  38.8730,  -77.0074,   25, 336, 402, 335,  101,  103, 1.01, orientation_deg= 30),
}


def park_for(code: str) -> ParkInfo:
    code = code.upper()
    if code not in PARKS:
        raise KeyError(f"Unknown park tricode: {code}")
    return PARKS[code]


def is_indoor(code: str, roof_open: bool | None = None) -> bool:
    """Decide whether weather features should be neutralised."""
    p = park_for(code)
    if p.is_dome:
        return True
    if p.is_retractable and roof_open is False:
        return True
    return False
