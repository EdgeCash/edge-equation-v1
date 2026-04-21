from pathlib import Path
import pytest

from edge_equation.persistence.db import Database
from edge_equation.stats.csv_loader import ResultsCsvLoader
from edge_equation.stats.results import GameResult, GameResultsStore


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


CSV_BODY = (
    "league,game_id,start_time,home_team,away_team,home_score,away_score,status\n"
    "KBO,K1,2026-04-13T18:30:00+09:00,Doosan Bears,LG Twins,5,3,final\n"
    "KBO,K2,2026-04-14T18:30:00+09:00,LG Twins,Doosan Bears,1,7,final\n"
)


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        ResultsCsvLoader.read("/tmp/no-such.csv")


def test_missing_required_columns_raises(tmp_path):
    bad = _write(tmp_path, "bad.csv", "league,game_id\nKBO,K1\n")
    with pytest.raises(ValueError, match="missing columns"):
        ResultsCsvLoader.read(bad)


def test_read_parses_rows(tmp_path):
    path = _write(tmp_path, "r.csv", CSV_BODY)
    results = ResultsCsvLoader.read(path)
    assert len(results) == 2
    assert results[0].game_id == "K1"
    assert results[0].home_team == "Doosan Bears"
    assert results[0].home_score == 5
    assert results[0].away_score == 3
    assert results[0].status == "final"


def test_read_status_defaults_to_final(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,home_score,away_score\n"
        "KBO,K1,2026-04-13T18:30:00+09:00,A,B,5,3\n"
    )
    path = _write(tmp_path, "r.csv", body)
    results = ResultsCsvLoader.read(path)
    assert results[0].status == "final"


def test_read_empty_game_id_raises(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,home_score,away_score,status\n"
        "KBO,,2026-04-13T18:30:00+09:00,A,B,5,3,final\n"
    )
    path = _write(tmp_path, "bad.csv", body)
    with pytest.raises(ValueError, match="empty game_id"):
        ResultsCsvLoader.read(path)


def test_load_file_upserts_to_db(conn, tmp_path):
    path = _write(tmp_path, "r.csv", CSV_BODY)
    ids = ResultsCsvLoader.load_file(conn, path)
    assert len(ids) == 2
    assert GameResultsStore.count_by_league(conn, "KBO") == 2


def test_load_file_second_call_upserts(conn, tmp_path):
    path = _write(tmp_path, "r.csv", CSV_BODY)
    ResultsCsvLoader.load_file(conn, path)
    ResultsCsvLoader.load_file(conn, path)  # same data again
    # Still only 2 rows; no duplicates
    assert GameResultsStore.count_by_league(conn, "KBO") == 2


def test_shipped_sample_results_parse_and_load(conn):
    sample = Path(__file__).resolve().parent.parent / "data" / "kbo_results_2026-04-13_to_2026-04-19.csv"
    assert sample.exists()
    ids = ResultsCsvLoader.load_file(conn, str(sample))
    assert len(ids) >= 5
    count = GameResultsStore.count_by_league(conn, "KBO")
    assert count == len(ids)
