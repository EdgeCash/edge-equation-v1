from datetime import date
import pytest

from edge_equation.ingestion.source_factory import (
    SourceFactory,
    LEAGUE_TO_ODDS_API_SPORT_KEY,
    _mock_source_for_league,
)
from edge_equation.ingestion.manual_csv_source import ManualCsvSource
from edge_equation.ingestion.odds_api_source import TheOddsApiSource
from edge_equation.ingestion.mlb_source import MlbLikeSource
from edge_equation.ingestion.nba_source import NbaSource
from edge_equation.ingestion.nfl_source import NflSource
from edge_equation.ingestion.nhl_source import NhlSource
from edge_equation.ingestion.soccer_source import SoccerSource
from edge_equation.ingestion.odds_api_client import API_KEY_ENV_VAR
from edge_equation.persistence.db import Database


RUN_DATE = date(2026, 4, 20)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ----------------------------------------------------- csv_path_for


def test_csv_path_for_default_dir():
    p = SourceFactory.csv_path_for("KBO", RUN_DATE)
    assert p.name == "kbo_2026-04-20.csv"
    assert p.parts[-2] == "data"


def test_csv_path_for_custom_dir(tmp_path):
    p = SourceFactory.csv_path_for("NPB", RUN_DATE, csv_dir=str(tmp_path))
    assert p.parent == tmp_path
    assert p.name == "npb_2026-04-20.csv"


# -------------------------------------------------- odds_api_key_set


def test_odds_api_key_set_explicit():
    assert SourceFactory.odds_api_key_set("abc") is True


def test_odds_api_key_set_missing(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    assert SourceFactory.odds_api_key_set(None) is False


def test_odds_api_key_set_env(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "envkey")
    assert SourceFactory.odds_api_key_set() is True


# ------------------------------------------------------ mock fallbacks


def test_mock_source_for_mlb():
    src = _mock_source_for_league("MLB")
    assert isinstance(src, MlbLikeSource)
    assert src.league == "MLB"


def test_mock_source_for_kbo():
    src = _mock_source_for_league("KBO")
    assert isinstance(src, MlbLikeSource)
    assert src.league == "KBO"


def test_mock_source_for_every_known_league():
    assert isinstance(_mock_source_for_league("NBA"), NbaSource)
    assert isinstance(_mock_source_for_league("NFL"), NflSource)
    assert isinstance(_mock_source_for_league("NHL"), NhlSource)
    assert isinstance(_mock_source_for_league("SOC"), SoccerSource)
    assert isinstance(_mock_source_for_league("NPB"), MlbLikeSource)


def test_mock_source_unknown_returns_none():
    assert _mock_source_for_league("CRICKET") is None


# ------------------------------------------------------ resolution priority


def test_csv_wins_when_file_exists(tmp_path, conn, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "would-be-used-but-csv-wins")
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "MLB,MLB-2026-04-20-DET-BOS,2026-04-20T13:05:00-04:00,BOS,DET,ML,BOS,,-140\n"
    )
    (tmp_path / "mlb_2026-04-20.csv").write_text(body, encoding="utf-8")

    src = SourceFactory.for_league("MLB", RUN_DATE, conn=conn, csv_dir=str(tmp_path))
    assert isinstance(src, ManualCsvSource)


def test_odds_api_used_when_key_set_and_no_csv(tmp_path, conn, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "envkey")
    src = SourceFactory.for_league("MLB", RUN_DATE, conn=conn, csv_dir=str(tmp_path))
    assert isinstance(src, TheOddsApiSource)
    assert src.sport_key == "baseball_mlb"


def test_mock_used_when_no_csv_and_no_api_key(tmp_path, conn, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    src = SourceFactory.for_league("MLB", RUN_DATE, conn=conn, csv_dir=str(tmp_path))
    assert isinstance(src, MlbLikeSource)


def test_mock_used_when_no_conn_even_if_api_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "envkey")
    src = SourceFactory.for_league("MLB", RUN_DATE, conn=None, csv_dir=str(tmp_path))
    assert isinstance(src, MlbLikeSource)


def test_mock_used_when_league_not_in_odds_api_map(tmp_path, conn, monkeypatch):
    # KBO isn't in the Odds API map -> always falls through to the mock even
    # with an API key.
    monkeypatch.setenv(API_KEY_ENV_VAR, "envkey")
    src = SourceFactory.for_league("KBO", RUN_DATE, conn=conn, csv_dir=str(tmp_path))
    assert isinstance(src, MlbLikeSource)
    assert src.league == "KBO"


def test_prefer_mock_forces_mock(tmp_path, conn, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "envkey")
    src = SourceFactory.for_league("MLB", RUN_DATE, conn=conn, csv_dir=str(tmp_path), prefer_mock=True)
    assert isinstance(src, MlbLikeSource)


def test_unknown_league_without_source_raises(tmp_path, conn, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    with pytest.raises(ValueError, match="No ingestion source"):
        SourceFactory.for_league("CRICKET", RUN_DATE, conn=conn, csv_dir=str(tmp_path))


# -------------------------------------------------- registry coverage


def test_odds_api_map_matches_known_sports():
    # Every mapped league has a corresponding mock fallback (so we can always
    # degrade gracefully).
    for league in LEAGUE_TO_ODDS_API_SPORT_KEY:
        assert _mock_source_for_league(league) is not None, league
