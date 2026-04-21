"""
Phase 9 end-to-end integration:

1. A mocked The-Odds-API request populates OddsCache; a second call hits the
   cache and doesn't re-fetch.
2. TheOddsApiSource + ingestion.normalizer + engine.betting_engine produce
   Picks from the cached payload.
3. Picks persist to PickStore tied to a SlateRecord.
4. ManualCsvSource loads a KBO CSV; the same engine flow generates + persists
   KBO Picks alongside the API-sourced ones.
5. Outcomes are recorded; RealizationTracker.settle_picks rolls them up.

Every step is deterministic and free of real network calls.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import pytest
import httpx

from edge_equation.ingestion.odds_api_client import TheOddsApiClient
from edge_equation.ingestion.odds_api_source import TheOddsApiSource
from edge_equation.ingestion.manual_csv_source import ManualCsvSource
from edge_equation.ingestion.normalizer import normalize_slate
from edge_equation.engine.pick_schema import Line
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.realization import RealizationTracker, SETTLED_WIN
from edge_equation.persistence.db import Database
from edge_equation.persistence.odds_cache import OddsCache
from edge_equation.persistence.slate_store import SlateStore, SlateRecord
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore


NOW = datetime(2026, 4, 20, 12, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _mock_api(payload_list, hit):
    def handler(request):
        hit["count"] += 1
        return httpx.Response(200, json=payload_list)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _odds_api_mlb_payload():
    return [
        {
            "id": "mlb_evt_1",
            "sport_key": "baseball_mlb",
            "commence_time": "2026-04-20T23:05:00Z",
            "home_team": "Boston Red Sox",
            "away_team": "Detroit Tigers",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Boston Red Sox", "price": -132},
                        {"name": "Detroit Tigers", "price": 112},
                    ]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over",  "price": -110, "point": 9.5},
                        {"name": "Under", "price": -110, "point": 9.5},
                    ]},
                ],
            }],
        },
    ]


def test_cache_first_prevents_second_network_call(conn):
    hit = {"count": 0}
    http = _mock_api(_odds_api_mlb_payload(), hit)

    p1 = TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h", "totals"],
        api_key="TEST", now=NOW, http_client=http,
    )
    p2 = TheOddsApiClient.fetch_odds(
        conn, sport_key="baseball_mlb", markets=["h2h", "totals"],
        api_key="TEST", now=NOW, http_client=http,
    )
    assert p1 == p2
    assert hit["count"] == 1


def test_odds_api_payload_generates_valid_slate(conn):
    key = TheOddsApiClient.cache_key("baseball_mlb", ["h2h", "totals"])
    OddsCache.put(conn, key, {"games": _odds_api_mlb_payload()}, ttl_seconds=900, now=NOW)

    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h", "totals"])
    games = source.get_raw_games(now=NOW)
    markets = source.get_raw_markets(now=NOW)

    slate = normalize_slate(games, markets)
    assert len(slate.games) == 1
    assert len(slate.markets) == 4  # 2 ML + 2 Total


def test_engine_consumes_odds_api_slate(conn):
    key = TheOddsApiClient.cache_key("baseball_mlb", ["h2h"])
    OddsCache.put(conn, key, {"games": _odds_api_mlb_payload()}, ttl_seconds=900, now=NOW)

    source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h"])
    markets = source.get_raw_markets(now=NOW)
    # Synthesize inputs for the engine; real pipeline has a feature-building step.
    home_ml = next(m for m in markets if m["market_type"] == "ML" and "Boston" in m["selection"])
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        selection=home_ml["selection"],
        game_id=home_ml["game_id"],
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=home_ml["odds"]))
    assert pick.fair_prob is not None
    assert pick.line.odds == -132


def test_manual_csv_source_feeds_engine(conn, tmp_path):
    csv_body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,Doosan Bears,,-140\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,Total,Over 9.5,9.5,-115\n"
    )
    csv_path = tmp_path / "kbo.csv"
    csv_path.write_text(csv_body, encoding="utf-8")
    source = ManualCsvSource(str(csv_path))

    slate = normalize_slate(source.get_raw_games(), source.get_raw_markets())
    assert len(slate.games) == 1
    assert slate.games[0].league == "KBO"
    assert slate.games[0].sport == "KBO"

    # Build + evaluate one of the markets via the engine
    bundle = FeatureBuilder.build(
        sport="KBO",
        market_type="ML",
        inputs={"strength_home": 1.25, "strength_away": 1.10, "home_adv": 0.09},
        universal_features={"home_edge": 0.04},
        selection="Doosan Bears",
        game_id="KBO-2026-04-20-LG-DB",
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-140))
    assert pick.sport == "KBO"
    assert pick.fair_prob is not None


def test_combined_flow_api_and_csv_persist_and_settle(conn, tmp_path):
    # Seed API cache for MLB
    key = TheOddsApiClient.cache_key("baseball_mlb", ["h2h"])
    OddsCache.put(conn, key, {"games": _odds_api_mlb_payload()}, ttl_seconds=900, now=NOW)
    mlb_source = TheOddsApiSource(conn, sport_key="baseball_mlb", markets=["h2h"])
    mlb_markets = mlb_source.get_raw_markets(now=NOW)

    # Write a KBO CSV
    csv_body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,Doosan Bears,,-140\n"
    )
    csv_path = tmp_path / "kbo.csv"
    csv_path.write_text(csv_body, encoding="utf-8")
    kbo_source = ManualCsvSource(str(csv_path))
    kbo_markets = kbo_source.get_raw_markets()

    # Record a slate that spans both sources
    slate_id = "daily_20260420"
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id,
        generated_at="2026-04-20T09:00:00",
        sport=None,
        card_type="daily_edge",
        metadata={"sources": ["theoddsapi", "manual_csv"]},
    ))

    # Pick the home ML for each source and persist
    mlb_home_ml = next(m for m in mlb_markets if m["market_type"] == "ML" and "Boston" in m["selection"])
    mlb_bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        selection=mlb_home_ml["selection"],
        game_id=mlb_home_ml["game_id"],
    )
    mlb_pick = BettingEngine.evaluate(mlb_bundle, Line(odds=mlb_home_ml["odds"]))
    kbo_home_ml = next(m for m in kbo_markets if m["market_type"] == "ML")
    kbo_bundle = FeatureBuilder.build(
        sport="KBO", market_type="ML",
        inputs={"strength_home": 1.25, "strength_away": 1.10, "home_adv": 0.09},
        universal_features={"home_edge": 0.04},
        selection=kbo_home_ml["selection"],
        game_id=kbo_home_ml["game_id"],
    )
    kbo_pick = BettingEngine.evaluate(kbo_bundle, Line(odds=kbo_home_ml["odds"]))

    PickStore.insert_many(conn, [mlb_pick, kbo_pick], slate_id=slate_id)

    # Settle both picks with wins
    RealizationStore.record_outcome(
        conn, mlb_home_ml["game_id"], "ML", mlb_home_ml["selection"], "win",
    )
    RealizationStore.record_outcome(
        conn, kbo_home_ml["game_id"], "ML", kbo_home_ml["selection"], "win",
    )
    result = RealizationTracker.settle_picks(conn, slate_id=slate_id)
    assert result == {"matched": 2, "updated": 2}

    rows = PickStore.list_by_slate(conn, slate_id)
    assert all(r.realization == SETTLED_WIN for r in rows)


def test_shipped_sample_csvs_feed_normalize_slate():
    data_dir = Path(__file__).resolve().parent.parent / "data"
    for name in ("kbo_2026-04-20.csv", "npb_2026-04-20.csv"):
        s = ManualCsvSource(str(data_dir / name))
        slate = normalize_slate(s.get_raw_games(), s.get_raw_markets())
        assert len(slate.games) >= 1
        assert len(slate.markets) >= 1
