#!/usr/bin/env bash
# apply_phase5_6a.sh
#
# Writes Phase-5 (FastAPI) + Phase-6A (Next.js website) files, installs
# Python deps, and runs both pytest suites.
# Exits non-zero on failure.
#
# Run from repo root of edge-equation-v1 on branch phase-5-6a-api-website.

set -euo pipefail

echo "=== Phase 5 + 6A: writing API layer + website skeleton ==="

ROOT_DIR="$(pwd)"
SRC="$ROOT_DIR/src"
TESTS="$ROOT_DIR/tests"
API="$ROOT_DIR/api"
TESTS_API="$ROOT_DIR/tests_api"
WEB="$ROOT_DIR/website"

mkdir -p "$API/routers" "$API/schemas"
mkdir -p "$TESTS_API"
mkdir -p "$WEB/pages" "$WEB/components" "$WEB/styles"

touch "$API/__init__.py" "$API/routers/__init__.py" "$API/schemas/__init__.py"

########################################
# API layer
########################################

cat > "$API/main.py" << 'FILE_EOF'
"""
FastAPI entrypoint for the Edge Equation API.

Run locally with:
    uvicorn api.main:app --reload
"""
from fastapi import FastAPI

from api.routers import cards, health, picks, premium, slate


def create_app() -> FastAPI:
    app = FastAPI(
        title="Edge Equation API",
        version="v1",
        description="Deterministic sports analytics engine. Facts. Not Feelings.",
    )
    app.include_router(health.router)
    app.include_router(picks.router)
    app.include_router(cards.router)
    app.include_router(premium.router)
    app.include_router(slate.router)
    return app


app = create_app()
FILE_EOF

cat > "$API/data_source.py" << 'FILE_EOF'
"""
Data-source helpers for the API.

Runtime: live mock sources keyed on the current date, deterministic per day.
Tests inject fixtures directly and do not touch these functions.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.nfl_source import NflSource
from edge_equation.ingestion.soccer_source import SoccerSource
from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.ingestion.schema import Slate, LEAGUE_TO_SPORT, VALID_LEAGUES
from edge_equation.engine.slate_runner import run_slate
from edge_equation.engine.pick_schema import Pick
from edge_equation.premium.mc_simulator import MonteCarloSimulator
from edge_equation.premium.premium_pick import PremiumPick


SPORT_ALIASES = {
    "mlb": "MLB", "kbo": "KBO", "npb": "NPB",
    "nba": "NBA", "ncaab": "NCAAB",
    "nhl": "NHL",
    "nfl": "NFL", "ncaaf": "NCAAF",
    "soc": "SOC", "soccer": "SOC",
}


def _resolve_sport(sport: str) -> str:
    """Resolve a user-provided sport identifier to a canonical league code."""
    if not sport:
        raise ValueError("sport is required")
    normalized = SPORT_ALIASES.get(sport.lower().strip())
    if normalized is None and sport.upper() in VALID_LEAGUES:
        normalized = sport.upper()
    if normalized is None:
        raise ValueError(f"Unknown sport: {sport!r}")
    return normalized


def _source_for_league(league: str):
    if league in ("MLB", "KBO", "NPB"):
        return MlbLikeSource(league)
    if league in ("NBA", "NCAAB"):
        return NbaSource()
    if league == "NHL":
        return NhlSource()
    if league in ("NFL", "NCAAF"):
        return NflSource()
    if league == "SOC":
        return SoccerSource()
    raise ValueError(f"No source wired for league: {league!r}")


def get_slate_for_league(league: str, run_datetime: Optional[datetime] = None) -> Slate:
    """Load a typed Slate for the given league at the given run datetime."""
    run_dt = run_datetime or datetime.now()
    source = _source_for_league(league)
    return normalize_slate(
        source.get_raw_games(run_dt),
        source.get_raw_markets(run_dt),
    )


def get_combined_slate_for_all_sports(run_datetime: Optional[datetime] = None) -> Slate:
    """Combined slate across the leagues the engine currently serves public cards on."""
    run_dt = run_datetime or datetime.now()
    games, markets = [], []
    for league in ("MLB", "NBA", "NHL"):
        source = _source_for_league(league)
        games.extend(source.get_raw_games(run_dt))
        markets.extend(source.get_raw_markets(run_dt))
    return normalize_slate(games, markets)


def picks_for_today(run_datetime: Optional[datetime] = None) -> List[Pick]:
    """Run the engine over today's combined slate and return picks."""
    run_dt = run_datetime or datetime.now()
    slate = get_combined_slate_for_all_sports(run_dt)
    all_picks: List[Pick] = []
    for sport_filter in ("MLB", "NBA", "NHL"):
        all_picks.extend(run_slate(slate, sport_filter, public_mode=False))
    return all_picks


def premium_picks_for_today(
    run_datetime: Optional[datetime] = None,
    seed: int = 42,
    iterations: int = 1000,
) -> List[PremiumPick]:
    """Wrap today's picks with MC-derived distributions."""
    picks = picks_for_today(run_datetime)
    sim = MonteCarloSimulator(seed=seed, iterations=iterations)
    premium: List[PremiumPick] = []
    for pick in picks:
        if pick.fair_prob is not None:
            # Binary / ML-like: simulate around the fair probability
            dist = sim.simulate_binary(pick.fair_prob)
            notes = f"MC binary simulation, seed={seed}, iterations={iterations}."
        elif pick.expected_value is not None:
            # Total or rate prop: use 15% of mean as placeholder stdev
            mean = pick.expected_value
            stdev = (mean * Decimal("0.15")).quantize(Decimal("0.01"))
            dist = sim.simulate_total(mean, stdev)
            notes = (
                f"MC total simulation, seed={seed}, iterations={iterations}, "
                f"placeholder stdev=15% of mean."
            )
        else:
            # Shouldn't normally happen; skip gracefully
            continue
        premium.append(PremiumPick(
            base_pick=pick,
            p10=dist["p10"], p50=dist["p50"],
            p90=dist["p90"], mean=dist["mean"],
            notes=notes,
        ))
    return premium


def pick_to_out_dict(pick: Pick) -> dict:
    """Flatten a Pick to a JSON-friendly dict for API responses."""
    return {
        "selection": pick.selection,
        "market_type": pick.market_type,
        "sport": pick.sport,
        "line_odds": pick.line.odds,
        "line_number": str(pick.line.number) if pick.line.number is not None else None,
        "fair_prob": str(pick.fair_prob) if pick.fair_prob is not None else None,
        "expected_value": str(pick.expected_value) if pick.expected_value is not None else None,
        "edge": str(pick.edge) if pick.edge is not None else None,
        "grade": pick.grade,
        "kelly": str(pick.kelly) if pick.kelly is not None else None,
        "realization": pick.realization,
        "game_id": pick.game_id,
        "event_time": pick.event_time,
    }


def premium_pick_to_out_dict(pp: PremiumPick) -> dict:
    base = pick_to_out_dict(pp.base_pick)
    base.update({
        "p10": str(pp.p10) if pp.p10 is not None else None,
        "p50": str(pp.p50) if pp.p50 is not None else None,
        "p90": str(pp.p90) if pp.p90 is not None else None,
        "mean": str(pp.mean) if pp.mean is not None else None,
        "notes": pp.notes,
    })
    return base


def slate_entries_for_sport(sport: str, run_datetime: Optional[datetime] = None) -> List[dict]:
    """Raw slate entries for a given sport path-param."""
    league = _resolve_sport(sport)
    slate = get_slate_for_league(league, run_datetime)
    # Group ML and Total markets per game into flat slate entries
    markets_by_game: dict = {}
    for m in slate.markets:
        markets_by_game.setdefault(m.game_id, []).append(m)
    entries = []
    for g in slate.games:
        ml_home = None
        ml_away = None
        total = None
        for m in markets_by_game.get(g.game_id, []):
            if m.market_type == "ML":
                if m.selection == g.home_team:
                    ml_home = m.odds
                elif m.selection == g.away_team:
                    ml_away = m.odds
                else:
                    # Default to home odds when selection ambiguous
                    if ml_home is None:
                        ml_home = m.odds
            elif m.market_type == "Total" and total is None and m.line is not None:
                total = str(m.line)
        entries.append({
            "game_id": g.game_id,
            "home_team": g.home_team,
            "away_team": g.away_team,
            "moneyline_home": ml_home,
            "moneyline_away": ml_away,
            "total": total,
            "event_time": g.start_time.isoformat(),
        })
    return entries
FILE_EOF

cat > "$API/schemas/health.py" << 'FILE_EOF'
"""Health schema."""
from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str
FILE_EOF

cat > "$API/schemas/pick.py" << 'FILE_EOF'
"""Pick schema exposed over the API.

Plain JSON-serializable fields. Decimals become strings to preserve
deterministic precision from the engine.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PickOut(BaseModel):
    selection: str
    market_type: str
    sport: str
    line_odds: int
    line_number: Optional[str] = None
    fair_prob: Optional[str] = None
    expected_value: Optional[str] = None
    edge: Optional[str] = None
    grade: str
    kelly: Optional[str] = None
    realization: int
    game_id: Optional[str] = None
    event_time: Optional[str] = None
FILE_EOF

cat > "$API/schemas/card.py" << 'FILE_EOF'
"""Card schema exposed over the API."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class CardOut(BaseModel):
    card_type: str
    headline: str
    subhead: str
    picks: List[dict]
    tagline: str
    generated_at: Optional[str] = None
FILE_EOF

cat > "$API/schemas/premium_pick.py" << 'FILE_EOF'
"""Premium pick schema exposed over the API."""
from typing import Optional

from pydantic import BaseModel

from api.schemas.pick import PickOut


class PremiumPickOut(BaseModel):
    selection: str
    market_type: str
    sport: str
    line_odds: int
    line_number: Optional[str] = None
    fair_prob: Optional[str] = None
    expected_value: Optional[str] = None
    edge: Optional[str] = None
    grade: str
    kelly: Optional[str] = None
    realization: int
    game_id: Optional[str] = None
    event_time: Optional[str] = None
    # Premium additions:
    p10: Optional[str] = None
    p50: Optional[str] = None
    p90: Optional[str] = None
    mean: Optional[str] = None
    notes: Optional[str] = None
FILE_EOF

cat > "$API/routers/health.py" << 'FILE_EOF'
"""Health router."""
from fastapi import APIRouter

from api.schemas.health import Health


router = APIRouter(tags=["health"])

API_VERSION = "v1"


@router.get("/health", response_model=Health)
def get_health() -> Health:
    return Health(status="ok", version=API_VERSION)
FILE_EOF

cat > "$API/routers/picks.py" << 'FILE_EOF'
"""Picks router."""
from typing import List

from fastapi import APIRouter

from api.data_source import picks_for_today, pick_to_out_dict


router = APIRouter(prefix="/picks", tags=["picks"])


@router.get("/today")
def get_picks_today() -> List[dict]:
    picks = picks_for_today()
    return [pick_to_out_dict(p) for p in picks]
FILE_EOF

cat > "$API/routers/cards.py" << 'FILE_EOF'
"""Cards router."""
from datetime import datetime

from fastapi import APIRouter

from edge_equation.engine.daily_scheduler import generate_daily_edge_card


router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("/daily")
def get_daily_card() -> dict:
    return generate_daily_edge_card(datetime.now())
FILE_EOF

cat > "$API/routers/premium.py" << 'FILE_EOF'
"""Premium router."""
from datetime import datetime
from typing import List

from fastapi import APIRouter

from api.data_source import premium_picks_for_today, premium_pick_to_out_dict
from edge_equation.premium.premium_cards import build_premium_daily_edge_card


router = APIRouter(prefix="/premium", tags=["premium"])


@router.get("/picks/today")
def get_premium_picks_today() -> List[dict]:
    premium = premium_picks_for_today()
    return [premium_pick_to_out_dict(pp) for pp in premium]


@router.get("/cards/daily")
def get_premium_card_daily() -> dict:
    premium = premium_picks_for_today()
    card = build_premium_daily_edge_card(premium)
    card.setdefault("generated_at", datetime.now().isoformat())
    return card
FILE_EOF

cat > "$API/routers/slate.py" << 'FILE_EOF'
"""Slate router."""
from typing import List

from fastapi import APIRouter, HTTPException

from api.data_source import slate_entries_for_sport


router = APIRouter(prefix="/slate", tags=["slate"])


@router.get("/{sport}")
def get_slate(sport: str) -> List[dict]:
    try:
        return slate_entries_for_sport(sport)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
FILE_EOF

########################################
# API tests
########################################

cat > "$TESTS_API/conftest.py" << 'FILE_EOF'
"""
Shared API test fixtures.

Uses pinned datetime (2026-04-20T09:00:00) so tests are independent of
wall-clock time. The api.data_source module is monkeypatched to use
this pinned datetime instead of datetime.now().
"""
import pytest
from datetime import datetime

from fastapi.testclient import TestClient

from api.main import app
from api import data_source as _ds


PINNED_RUN_DT = datetime(2026, 4, 20, 9, 0, 0)


@pytest.fixture(autouse=True)
def pin_clock(monkeypatch):
    """Monkeypatch datetime.now() usage inside data_source and routers."""
    # Wrap the existing helpers so any routers or code relying on them
    # see a deterministic clock.
    real_picks_for_today = _ds.picks_for_today
    real_premium_picks_for_today = _ds.premium_picks_for_today
    real_slate_entries_for_sport = _ds.slate_entries_for_sport

    def pinned_picks(run_datetime=None):
        return real_picks_for_today(run_datetime or PINNED_RUN_DT)

    def pinned_premium(run_datetime=None, seed=42, iterations=1000):
        return real_premium_picks_for_today(run_datetime or PINNED_RUN_DT, seed, iterations)

    def pinned_slate_entries(sport, run_datetime=None):
        return real_slate_entries_for_sport(sport, run_datetime or PINNED_RUN_DT)

    monkeypatch.setattr(_ds, "picks_for_today", pinned_picks)
    monkeypatch.setattr(_ds, "premium_picks_for_today", pinned_premium)
    monkeypatch.setattr(_ds, "slate_entries_for_sport", pinned_slate_entries)

    # Patch names imported into router modules
    import api.routers.picks as _r_picks
    import api.routers.premium as _r_premium
    import api.routers.slate as _r_slate
    monkeypatch.setattr(_r_picks, "picks_for_today", pinned_picks)
    monkeypatch.setattr(_r_premium, "premium_picks_for_today", pinned_premium)
    monkeypatch.setattr(_r_slate, "slate_entries_for_sport", pinned_slate_entries)

    # Pin datetime.now() used by cards.py and premium.py card endpoint
    import api.routers.cards as _r_cards
    import api.routers.premium as _r_premium2

    class _PinnedDatetime:
        @staticmethod
        def now():
            return PINNED_RUN_DT

    monkeypatch.setattr(_r_cards, "datetime", _PinnedDatetime)
    monkeypatch.setattr(_r_premium2, "datetime", _PinnedDatetime)


@pytest.fixture
def client():
    return TestClient(app)
FILE_EOF

cat > "$TESTS_API/test_health.py" << 'FILE_EOF'
def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "v1"


def test_health_schema(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"status", "version"}
FILE_EOF

cat > "$TESTS_API/test_picks.py" << 'FILE_EOF'
REQUIRED_KEYS = {
    "selection", "market_type", "sport",
    "line_odds", "line_number",
    "fair_prob", "expected_value", "edge", "grade", "kelly",
    "realization", "game_id", "event_time",
}


def test_picks_today_200(client):
    r = client.get("/picks/today")
    assert r.status_code == 200


def test_picks_today_returns_list(client):
    r = client.get("/picks/today")
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0


def test_picks_today_schema(client):
    r = client.get("/picks/today")
    body = r.json()
    for pick in body:
        assert set(pick.keys()) == REQUIRED_KEYS
        assert isinstance(pick["selection"], str)
        assert isinstance(pick["market_type"], str)
        assert isinstance(pick["grade"], str)
        assert isinstance(pick["line_odds"], int)
        assert isinstance(pick["realization"], int)


def test_picks_today_deterministic(client):
    r1 = client.get("/picks/today").json()
    r2 = client.get("/picks/today").json()
    assert r1 == r2


def test_picks_contains_known_det_at_bos_ml(client):
    r = client.get("/picks/today").json()
    # The DET @ BOS ML is our canonical reference pick.
    ml = [p for p in r if p["market_type"] == "ML" and p["selection"] == "BOS"
          and p["sport"] == "MLB"]
    assert ml, "Expected to find BOS ML in MLB picks"
    pick = ml[0]
    assert pick["fair_prob"] == "0.618133"
    assert pick["edge"] == "0.049167"
    assert pick["grade"] == "A"
    assert pick["kelly"] == "0.0324"
    assert pick["realization"] == 59
FILE_EOF

cat > "$TESTS_API/test_cards.py" << 'FILE_EOF'
REQUIRED_KEYS = {"card_type", "headline", "subhead", "picks", "tagline"}


def test_cards_daily_200(client):
    r = client.get("/cards/daily")
    assert r.status_code == 200


def test_cards_daily_schema(client):
    r = client.get("/cards/daily")
    body = r.json()
    for k in REQUIRED_KEYS:
        assert k in body, f"missing key: {k}"
    assert body["tagline"] == "Facts. Not Feelings."
    assert isinstance(body["picks"], list)


def test_cards_daily_card_type(client):
    r = client.get("/cards/daily")
    body = r.json()
    assert body["card_type"] == "daily_edge"


def test_cards_daily_deterministic(client):
    r1 = client.get("/cards/daily").json()
    r2 = client.get("/cards/daily").json()
    assert r1 == r2
FILE_EOF

cat > "$TESTS_API/test_premium.py" << 'FILE_EOF'
PREMIUM_REQUIRED_KEYS = {
    "selection", "market_type", "sport",
    "line_odds", "line_number",
    "fair_prob", "expected_value", "edge", "grade", "kelly",
    "realization", "game_id", "event_time",
    "p10", "p50", "p90", "mean", "notes",
}


def test_premium_picks_200(client):
    r = client.get("/premium/picks/today")
    assert r.status_code == 200


def test_premium_picks_schema(client):
    r = client.get("/premium/picks/today")
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0
    for pp in body:
        assert set(pp.keys()) == PREMIUM_REQUIRED_KEYS
        # Distribution fields must be present (p10/p50/p90/mean)
        assert pp["p10"] is not None
        assert pp["p50"] is not None
        assert pp["p90"] is not None
        assert pp["mean"] is not None


def test_premium_picks_deterministic(client):
    r1 = client.get("/premium/picks/today").json()
    r2 = client.get("/premium/picks/today").json()
    assert r1 == r2


def test_premium_cards_daily_200(client):
    r = client.get("/premium/cards/daily")
    assert r.status_code == 200


def test_premium_cards_daily_schema(client):
    r = client.get("/premium/cards/daily").json()
    assert r["card_type"] == "premium_daily_edge"
    assert r["headline"] == "Premium Daily Edge"
    assert r["subhead"] == "Full distributions and model notes."
    assert r["tagline"] == "Facts. Not Feelings."
    assert isinstance(r["picks"], list)
    assert len(r["picks"]) > 0


def test_premium_cards_daily_deterministic(client):
    r1 = client.get("/premium/cards/daily").json()
    r2 = client.get("/premium/cards/daily").json()
    # generated_at will be equal since we pinned datetime.now()
    assert r1 == r2
FILE_EOF

cat > "$TESTS_API/test_slate.py" << 'FILE_EOF'
REQUIRED_KEYS = {
    "game_id", "home_team", "away_team",
    "moneyline_home", "moneyline_away", "total", "event_time",
}


def test_slate_mlb_200(client):
    r = client.get("/slate/mlb")
    assert r.status_code == 200


def test_slate_mlb_returns_list(client):
    r = client.get("/slate/mlb").json()
    assert isinstance(r, list)
    assert len(r) > 0


def test_slate_mlb_schema(client):
    r = client.get("/slate/mlb").json()
    for entry in r:
        assert set(entry.keys()) == REQUIRED_KEYS
        assert isinstance(entry["game_id"], str)
        assert isinstance(entry["home_team"], str)
        assert isinstance(entry["away_team"], str)


def test_slate_nba_200(client):
    r = client.get("/slate/nba")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_slate_nhl_200(client):
    r = client.get("/slate/nhl")
    assert r.status_code == 200


def test_slate_uppercase_sport(client):
    r = client.get("/slate/MLB")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_slate_soccer_alias(client):
    r = client.get("/slate/soccer")
    assert r.status_code == 200


def test_slate_unknown_sport_404(client):
    r = client.get("/slate/cricket")
    assert r.status_code == 404


def test_slate_deterministic(client):
    r1 = client.get("/slate/mlb").json()
    r2 = client.get("/slate/mlb").json()
    assert r1 == r2


def test_slate_first_mlb_entry_matches_source(client):
    r = client.get("/slate/mlb").json()
    first = r[0]
    assert first["game_id"] == "MLB-2026-04-20-DET-BOS"
    assert first["home_team"] == "BOS"
    assert first["away_team"] == "DET"
    assert first["moneyline_home"] == -132
    assert first["total"] == "9.5"
FILE_EOF

########################################
# Website config
########################################

cat > "$WEB/package.json" << 'FILE_EOF'
{
  "name": "edge-equation-web",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "14.2.5",
    "react": "18.3.1",
    "react-dom": "18.3.1"
  },
  "devDependencies": {
    "@types/node": "20.14.10",
    "@types/react": "18.3.3",
    "@types/react-dom": "18.3.0",
    "autoprefixer": "10.4.19",
    "eslint": "8.57.0",
    "eslint-config-next": "14.2.5",
    "postcss": "8.4.39",
    "tailwindcss": "3.4.6",
    "typescript": "5.5.3"
  }
}
FILE_EOF

cat > "$WEB/next.config.js" << 'FILE_EOF'
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    // Placeholder; wired up in Phase 6B
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "",
  },
};

module.exports = nextConfig;
FILE_EOF

cat > "$WEB/tailwind.config.js" << 'FILE_EOF'
/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./pages/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#08090b",
          900: "#0d0f12",
          800: "#14171c",
          700: "#1e2229",
          600: "#2a2f38",
          500: "#3a4050",
        },
        edge: {
          accent: "#d7b572",        // warm gold — restrained, editorial
          accentMuted: "#a68a55",
          line: "#242932",
          text: "#e7e3d8",
          textDim: "#8a8a7a",
        },
      },
      fontFamily: {
        display: ["'Fraunces'", "ui-serif", "Georgia", "serif"],
        body: ["'Inter Tight'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      maxWidth: {
        prose: "68ch",
      },
    },
  },
  plugins: [],
};
FILE_EOF

cat > "$WEB/postcss.config.js" << 'FILE_EOF'
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
FILE_EOF

cat > "$WEB/tsconfig.json" << 'FILE_EOF'
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./*"]
    }
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    "**/*.tsx"
  ],
  "exclude": ["node_modules"]
}
FILE_EOF

cat > "$WEB/next-env.d.ts" << 'FILE_EOF'
/// <reference types="next" />
/// <reference types="next/image-types/global" />

// NOTE: This file should not be edited
// see https://nextjs.org/docs/basic-features/typescript for more information.
FILE_EOF

cat > "$WEB/README.md" << 'FILE_EOF'
# Edge Equation — Web

The public-facing website. Next.js (TypeScript) + Tailwind CSS.

## Local dev

```bash
cd website
npm install
npm run dev
```

Open http://localhost:3000.

## Pages

- `/` — Home
- `/daily-edge` — Public Daily Edge card (placeholder; wired up in Phase 6B)
- `/premium-edge` — Premium teaser
- `/about` — Manifesto
- `/contact` — Email + social

## Stack

- Next.js 14 (pages router)
- React 18
- TypeScript 5
- Tailwind CSS 3 (dark theme via `darkMode: "class"`, background applied via `<body>`)
- Fraunces (display) + Inter Tight (body) + JetBrains Mono (accent), loaded from Google Fonts

## Environment

`NEXT_PUBLIC_API_URL` — reserved for Phase 6B. Currently unused.

## Deploy

Deployed via Vercel with the monorepo `vercel.json` at the repo root.
Root directory: `website/`.

## Design philosophy

Editorial, restrained, dark. Warm gold accent (`#d7b572`) on near-black
(`#08090b`). Serif display, monospace for labels and data, sans-serif
for body. Tick marks in card corners, tabular numerals, subtle radial
gradients in the background. Facts. Not Feelings.
FILE_EOF

cat > "$WEB/.gitignore" << 'FILE_EOF'
node_modules/
.next/
out/
.env.local
.env.development.local
.env.production.local
.vercel
*.log
FILE_EOF

########################################
# Website styles + components
########################################

cat > "$WEB/styles/globals.css" << 'FILE_EOF'
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Import editorial + technical fonts */
@import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,500;9..144,600&family=Inter+Tight:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap");

@layer base {
  html {
    color-scheme: dark;
  }

  body {
    @apply bg-ink-950 text-edge-text font-body antialiased;
    font-feature-settings: "ss01", "cv11";
    background-image:
      radial-gradient(1200px 600px at 10% -10%, rgba(215, 181, 114, 0.06), transparent 60%),
      radial-gradient(800px 500px at 110% 10%, rgba(215, 181, 114, 0.03), transparent 60%);
    background-attachment: fixed;
  }

  h1, h2, h3 {
    @apply font-display text-edge-text;
    font-variation-settings: "opsz" 120, "SOFT" 30;
  }

  ::selection {
    @apply bg-edge-accent text-ink-950;
  }
}

@layer components {
  .hairline {
    @apply border-t border-edge-line;
  }

  .tabular {
    font-variant-numeric: tabular-nums;
  }
}
FILE_EOF

cat > "$WEB/components/Header.tsx" << 'FILE_EOF'
import Link from "next/link";

const nav = [
  { href: "/", label: "Home" },
  { href: "/daily-edge", label: "Daily Edge" },
  { href: "/premium-edge", label: "Premium" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

export default function Header() {
  return (
    <header className="border-b border-edge-line">
      <div className="mx-auto max-w-6xl px-6 py-6 flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <Link href="/" className="group">
          <div className="flex items-baseline gap-3">
            <span className="font-display text-2xl tracking-tightest text-edge-text">
              Edge Equation
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-accent">
              v1
            </span>
          </div>
          <div className="font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textDim mt-1">
            Facts. Not Feelings.
          </div>
        </Link>
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textDim hover:text-edge-accent transition-colors"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
FILE_EOF

cat > "$WEB/components/Footer.tsx" << 'FILE_EOF'
import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-edge-line mt-24">
      <div className="mx-auto max-w-6xl px-6 py-10 flex flex-col sm:flex-row gap-4 sm:items-center sm:justify-between">
        <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-edge-textDim">
          © {new Date().getFullYear()} Edge Equation
        </div>
        <div className="flex gap-6 font-mono text-[11px] uppercase tracking-[0.2em]">
          <Link
            href="https://x.com/edgeequation"
            className="text-edge-textDim hover:text-edge-accent transition-colors"
          >
            X / Twitter
          </Link>
          <Link
            href="mailto:contact@edgeequation.com"
            className="text-edge-textDim hover:text-edge-accent transition-colors"
          >
            Contact
          </Link>
        </div>
      </div>
    </footer>
  );
}
FILE_EOF

cat > "$WEB/components/Layout.tsx" << 'FILE_EOF'
import Head from "next/head";
import { ReactNode } from "react";

import Header from "./Header";
import Footer from "./Footer";

type LayoutProps = {
  children: ReactNode;
  title?: string;
  description?: string;
};

export default function Layout({
  children,
  title = "Edge Equation",
  description = "Deterministic sports analytics. Facts. Not Feelings.",
}: LayoutProps) {
  const pageTitle =
    title === "Edge Equation" ? title : `${title} — Edge Equation`;
  return (
    <>
      <Head>
        <title>{pageTitle}</title>
        <meta name="description" content={description} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#08090b" />
      </Head>
      <div className="min-h-screen flex flex-col">
        <Header />
        <main className="flex-1">
          <div className="mx-auto max-w-6xl px-6 py-16">{children}</div>
        </main>
        <Footer />
      </div>
    </>
  );
}
FILE_EOF

cat > "$WEB/components/CardShell.tsx" << 'FILE_EOF'
import { ReactNode } from "react";

type CardShellProps = {
  headline?: string;
  subhead?: string;
  eyebrow?: string;
  children: ReactNode;
};

export default function CardShell({
  headline,
  subhead,
  eyebrow,
  children,
}: CardShellProps) {
  return (
    <article className="relative bg-ink-900/80 backdrop-blur border border-edge-line rounded-sm p-8 sm:p-10">
      {/* Corner tick marks for editorial feel */}
      <div className="absolute top-0 left-0 w-3 h-3 border-l border-t border-edge-accent/60" />
      <div className="absolute top-0 right-0 w-3 h-3 border-r border-t border-edge-accent/60" />
      <div className="absolute bottom-0 left-0 w-3 h-3 border-l border-b border-edge-accent/60" />
      <div className="absolute bottom-0 right-0 w-3 h-3 border-r border-b border-edge-accent/60" />

      {eyebrow && (
        <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-edge-accent mb-4">
          {eyebrow}
        </div>
      )}
      {headline && (
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-[1.05] text-edge-text">
          {headline}
        </h2>
      )}
      {subhead && (
        <p className="mt-3 text-edge-textDim max-w-prose">{subhead}</p>
      )}
      <div className="mt-8">{children}</div>
    </article>
  );
}
FILE_EOF

########################################
# Website pages
########################################

cat > "$WEB/pages/_app.tsx" << 'FILE_EOF'
import type { AppProps } from "next/app";
import "@/styles/globals.css";

export default function App({ Component, pageProps }: AppProps) {
  return <Component {...pageProps} />;
}
FILE_EOF

cat > "$WEB/pages/index.tsx" << 'FILE_EOF'
import Link from "next/link";
import Layout from "@/components/Layout";

export default function Home() {
  return (
    <Layout>
      <section className="pt-10 sm:pt-16">
        <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-6">
          Deterministic Sports Analytics · Est. 2026
        </div>
        <h1 className="font-display font-light text-[clamp(3rem,8vw,6.5rem)] leading-[0.95] tracking-tightest">
          Edge <span className="italic text-edge-accent">Equation</span>
        </h1>
        <p className="mt-8 max-w-prose text-edge-textDim text-lg leading-relaxed">
          A formula-driven engine that turns sport-specific inputs into fair
          probabilities, graded edges, and sized positions — no hype, no
          narrative. The same inputs always produce the same output.
        </p>

        <div className="mt-12 flex flex-wrap items-center gap-6">
          <Link
            href="/daily-edge"
            className="group inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-text transition-colors"
          >
            View Today&apos;s Edge
            <span className="group-hover:translate-x-1 transition-transform">→</span>
          </Link>
          <Link
            href="/about"
            className="font-mono text-xs uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-text transition-colors border-b border-transparent hover:border-edge-accent pb-1"
          >
            How It Works
          </Link>
        </div>
      </section>

      {/* Three-column principle grid */}
      <section className="mt-32 grid grid-cols-1 md:grid-cols-3 gap-px bg-edge-line">
        {[
          {
            num: "01",
            title: "Deterministic",
            body: "Fixed seeds, Decimal math, 28-digit precision. No wobble between runs.",
          },
          {
            num: "02",
            title: "Transparent",
            body: "Every pick carries fair probability, edge, grade, and Kelly. Show your work.",
          },
          {
            num: "03",
            title: "Disciplined",
            body: "Half-Kelly, capped at 25%. Clamps on impact and multipliers. Facts. Not Feelings.",
          },
        ].map((p) => (
          <div key={p.num} className="bg-ink-950 p-8">
            <div className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
              {p.num}
            </div>
            <h3 className="mt-4 font-display text-2xl tracking-tightest">{p.title}</h3>
            <p className="mt-3 text-edge-textDim">{p.body}</p>
          </div>
        ))}
      </section>
    </Layout>
  );
}
FILE_EOF

cat > "$WEB/pages/daily-edge.tsx" << 'FILE_EOF'
import Link from "next/link";
import Layout from "@/components/Layout";
import CardShell from "@/components/CardShell";

export default function DailyEdge() {
  return (
    <Layout title="Daily Edge" description="Today's public Daily Edge card from the Edge Equation engine.">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Public · Free
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Daily Edge
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        A single card, once a day. Model-driven projections across today&apos;s
        slate — fair probability, edge, grade, half-Kelly sizing.
      </p>

      <div className="mt-12">
        <CardShell
          eyebrow="Today · Public Card"
          headline="Today's Daily Edge"
          subhead="Model-driven projections across today's slate."
        >
          <div className="font-mono text-xs tracking-wide text-edge-textDim space-y-4">
            <div className="border border-dashed border-edge-line rounded-sm p-6">
              <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
                Placeholder
              </div>
              <p className="text-edge-text font-body text-base leading-relaxed">
                API-connected card coming in Phase 6B. Until then, cards ship
                once a day on X, with full picks, grades, and sizing.
              </p>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-4">
              {[
                { label: "Fair Prob", value: "0.618133" },
                { label: "Edge", value: "0.049167" },
                { label: "Grade", value: "A" },
                { label: "½ Kelly", value: "0.0324" },
              ].map((s) => (
                <div key={s.label}>
                  <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                    {s.label}
                  </div>
                  <div className="mt-1 font-mono tabular text-edge-text text-lg">
                    {s.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </CardShell>
      </div>

      <p className="mt-8 text-edge-textDim">
        Follow on{" "}
        <Link
          href="https://x.com/edgeequation"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          X
        </Link>{" "}
        for live cards.
      </p>
    </Layout>
  );
}
FILE_EOF

cat > "$WEB/pages/premium-edge.tsx" << 'FILE_EOF'
import Link from "next/link";
import Layout from "@/components/Layout";
import CardShell from "@/components/CardShell";

const FEATURES = [
  { title: "Full Distributions", body: "p10 / p50 / p90 and mean from deterministic Monte Carlo simulation." },
  { title: "Letter Grades", body: "A+ / A / B / C ratings on every pick, calibrated to realization buckets." },
  { title: "Kelly Guidance", body: "Half-Kelly, 25% cap, gated at meaningful edge. Sizing built in." },
  { title: "Model Notes", body: "Context on what drove the number and what would change it." },
];

export default function PremiumEdge() {
  return (
    <Layout title="Premium Edge" description="Premium analytics coming soon.">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Launching Soon
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Premium <span className="italic text-edge-accent">Edge</span>
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        Distributions, grades, sizing, and model notes. The same engine that
        powers the public card, with everything unredacted.
      </p>

      <div className="mt-12 grid grid-cols-1 md:grid-cols-2 gap-px bg-edge-line">
        {FEATURES.map((f, i) => (
          <div key={f.title} className="bg-ink-900 p-8">
            <div className="flex items-baseline gap-3 mb-3">
              <span className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                {String(i + 1).padStart(2, "0")}
              </span>
              <h3 className="font-display text-2xl tracking-tightest">{f.title}</h3>
            </div>
            <p className="text-edge-textDim">{f.body}</p>
          </div>
        ))}
      </div>

      <div className="mt-12">
        <CardShell eyebrow="Premium · Coming Soon" headline="Premium analytics launching soon.">
          <div className="space-y-4 text-edge-textDim">
            <p>
              Every Pick carries a deterministic Monte Carlo distribution. The
              card below is a preview of the shape premium subscribers will see.
            </p>
            <div className="hairline pt-4 flex flex-wrap items-center gap-3">
              <Link
                href="https://x.com/edgeequation"
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-text transition-colors"
              >
                Follow on X for launch updates
              </Link>
              <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim">
                No signup yet · No spam
              </span>
            </div>
          </div>
        </CardShell>
      </div>
    </Layout>
  );
}
FILE_EOF

cat > "$WEB/pages/about.tsx" << 'FILE_EOF'
import Layout from "@/components/Layout";

export default function About() {
  return (
    <Layout title="About">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Manifesto
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        About Edge <span className="italic text-edge-accent">Equation</span>
      </h1>

      <div className="mt-12 grid grid-cols-1 md:grid-cols-[1fr_auto_2fr] gap-10 items-start">
        <aside className="md:sticky md:top-12">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-textDim">
            Core Principle
          </div>
          <blockquote className="mt-3 font-display text-3xl tracking-tightest italic text-edge-accent leading-tight">
            &ldquo;Facts.
            <br />
            Not Feelings.&rdquo;
          </blockquote>
        </aside>

        <div className="hidden md:block w-px bg-edge-line self-stretch" />

        <div className="space-y-6 text-edge-textDim max-w-prose leading-relaxed">
          <p>
            Edge Equation is a deterministic sports analytics engine. Given the
            same inputs, it always produces the same output — no hidden
            randomness, no warm-takes, no narrative shortcuts. Every pick is
            the result of explicit math: Bradley-Terry for matchups, Dixon-Coles
            adjustments for totals, Poisson for BTTS, Kelly for sizing, all
            running on 28-digit Decimal precision.
          </p>
          <p>
            There is no hype here. We don&apos;t sell &ldquo;locks,&rdquo;
            &ldquo;heaters,&rdquo; or &ldquo;gamblers&rsquo;
            intuition.&rdquo; We publish fair probabilities, edge relative to
            market, grades, and sized positions. When the model is wrong, the
            record shows it. When it&apos;s right, the record shows that too.
          </p>
          <p>
            The edge isn&apos;t magic. It&apos;s discipline — a formula you can
            audit, executed the same way every day.
          </p>
        </div>
      </div>
    </Layout>
  );
}
FILE_EOF

cat > "$WEB/pages/contact.tsx" << 'FILE_EOF'
import Link from "next/link";
import Layout from "@/components/Layout";

export default function Contact() {
  return (
    <Layout title="Contact">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Get In Touch
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Contact
      </h1>

      <p className="mt-6 max-w-prose text-edge-textDim leading-relaxed">
        For partnerships, press, or data inquiries, reach out via email. For
        everything else — daily cards, model takes, and launch updates — X is
        the fastest way to stay in the loop.
      </p>

      <div className="mt-14 grid grid-cols-1 md:grid-cols-2 gap-px bg-edge-line">
        <div className="bg-ink-900 p-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-edge-accent">
            Email
          </div>
          <Link
            href="mailto:contact@edgeequation.com"
            className="mt-3 block font-display text-3xl tracking-tightest text-edge-text hover:text-edge-accent transition-colors"
          >
            contact@
            <wbr />
            edgeequation.com
          </Link>
          <p className="mt-3 text-edge-textDim text-sm">
            Partnerships, press, data.
          </p>
        </div>

        <div className="bg-ink-900 p-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-edge-accent">
            Social
          </div>
          <Link
            href="https://x.com/edgeequation"
            className="mt-3 block font-display text-3xl tracking-tightest text-edge-text hover:text-edge-accent transition-colors"
          >
            @edgeequation
          </Link>
          <p className="mt-3 text-edge-textDim text-sm">
            Daily cards, model takes, launch updates.
          </p>
        </div>
      </div>
    </Layout>
  );
}
FILE_EOF

########################################
# Monorepo Vercel config
########################################

cat > "$ROOT_DIR/vercel.json" << 'FILE_EOF'
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "buildCommand": "cd website && npm run build",
  "devCommand": "cd website && npm run dev",
  "installCommand": "cd website && npm install",
  "outputDirectory": "website/.next",
  "framework": "nextjs"
}
FILE_EOF

echo ""
echo "=== Phase 5 + 6A files written. ==="

# ------------------------------------------------------------------
# Python deps and test run
# ------------------------------------------------------------------
echo ""
echo "=== Installing Python API dependencies (fastapi, httpx, pytest) ==="
if python -m pip install --quiet fastapi "httpx>=0.25" pytest pydantic 2>/dev/null; then
  echo "Python deps installed."
else
  echo "WARNING: pip install failed. Install manually:"
  echo "  python -m pip install fastapi httpx pytest pydantic"
fi

echo ""
echo "=== Running engine + ingestion + publisher + premium tests (tests/) ==="
if command -v pytest >/dev/null 2>&1; then
  if ! pytest tests/ -v; then
    echo ""
    echo "ERROR: engine tests failed." >&2
    exit 1
  fi
else
  echo "WARNING: pytest not installed; skipping engine tests."
fi

echo ""
echo "=== Running API tests (tests_api/) ==="
if command -v pytest >/dev/null 2>&1; then
  if ! pytest tests_api/ -v; then
    echo ""
    echo "ERROR: API tests failed." >&2
    exit 1
  fi
else
  echo "WARNING: pytest not installed; skipping API tests."
fi

echo ""
echo "=== Phase 5 + 6A complete ==="
echo ""
echo "Website quick start:"
echo "  cd website && npm install && npm run dev"
echo "  Open http://localhost:3000"
