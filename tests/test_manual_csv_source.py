from decimal import Decimal
from pathlib import Path
import pytest

from edge_equation.ingestion.manual_csv_source import (
    ManualCsvSource,
    REQUIRED_COLUMNS,
)


def _write_csv(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ManualCsvSource(str(tmp_path / "nope.csv"))


def test_missing_required_columns_raises(tmp_path):
    path = _write_csv(tmp_path, "bad.csv",
        "league,game_id\nKBO,g1\n"
    )
    s = ManualCsvSource(path)
    with pytest.raises(ValueError, match="missing required columns"):
        s.get_raw_games()


def test_load_simple_kbo_slate(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,Doosan Bears,,-140\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,LG Twins,,+120\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,Total,Over 9.5,9.5,-115\n"
    )
    path = _write_csv(tmp_path, "kbo.csv", body)
    s = ManualCsvSource(path)
    games = s.get_raw_games()
    markets = s.get_raw_markets()
    assert len(games) == 1
    g = games[0]
    assert g["league"] == "KBO"
    assert g["game_id"] == "KBO-2026-04-20-LG-DB"
    assert g["home_team"] == "Doosan Bears"
    assert len(markets) == 3
    ml_picks = [m for m in markets if m["market_type"] == "ML"]
    assert len(ml_picks) == 2
    total_pick = next(m for m in markets if m["market_type"] == "Total")
    assert total_pick["line"] == Decimal("9.5")
    assert total_pick["odds"] == -115


def test_multiple_games_grouped_by_game_id(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,G1,2026-04-20T18:00:00+09:00,Home1,Away1,ML,Home1,,-140\n"
        "KBO,G2,2026-04-20T19:00:00+09:00,Home2,Away2,ML,Home2,,+110\n"
        "KBO,G1,2026-04-20T18:00:00+09:00,Home1,Away1,Total,Over 9,9,-110\n"
    )
    path = _write_csv(tmp_path, "two.csv", body)
    s = ManualCsvSource(path)
    games = s.get_raw_games()
    assert len(games) == 2
    ids = [g["game_id"] for g in games]
    assert ids == ["G1", "G2"]  # order preserved from first appearance


def test_empty_line_cell_yields_none(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,G1,2026-04-20T18:00:00+09:00,H,A,ML,H,,-140\n"
    )
    path = _write_csv(tmp_path, "ml_only.csv", body)
    s = ManualCsvSource(path)
    markets = s.get_raw_markets()
    assert markets[0]["line"] is None
    assert markets[0]["odds"] == -140


def test_empty_odds_cell_yields_none(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,G1,2026-04-20T18:00:00+09:00,H,A,ML,H,,\n"
    )
    path = _write_csv(tmp_path, "no_odds.csv", body)
    s = ManualCsvSource(path)
    markets = s.get_raw_markets()
    assert markets[0]["odds"] is None


def test_empty_game_id_raises(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,,2026-04-20T18:00:00+09:00,H,A,ML,H,,-140\n"
    )
    path = _write_csv(tmp_path, "bad.csv", body)
    s = ManualCsvSource(path)
    with pytest.raises(ValueError, match="empty game_id"):
        s.get_raw_games()


def test_empty_selection_raises(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,G1,2026-04-20T18:00:00+09:00,H,A,ML,,,-140\n"
    )
    path = _write_csv(tmp_path, "bad.csv", body)
    s = ManualCsvSource(path)
    with pytest.raises(ValueError, match="empty selection"):
        s.get_raw_markets()


def test_whitespace_stripped_from_cells(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        " KBO , G1 , 2026-04-20T18:00:00+09:00 , Home , Away , ML , Home , , -140 \n"
    )
    path = _write_csv(tmp_path, "ws.csv", body)
    s = ManualCsvSource(path)
    g = s.get_raw_games()[0]
    assert g["league"] == "KBO"
    assert g["home_team"] == "Home"
    m = s.get_raw_markets()[0]
    assert m["selection"] == "Home"
    assert m["odds"] == -140


def test_markets_meta_includes_source_and_path(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,G1,2026-04-20T18:00:00+09:00,H,A,ML,H,,-140\n"
    )
    path = _write_csv(tmp_path, "meta.csv", body)
    s = ManualCsvSource(path)
    m = s.get_raw_markets()[0]
    assert m["meta"]["source"] == "manual_csv"
    assert m["meta"]["path"].endswith("meta.csv")


def test_required_columns_list():
    assert set(REQUIRED_COLUMNS) == {
        "league", "game_id", "start_time", "home_team", "away_team",
        "market_type", "selection", "line", "odds",
    }


def test_shipped_sample_csvs_parse():
    repo_data = Path(__file__).resolve().parent.parent / "data"
    kbo = repo_data / "kbo_2026-04-20.csv"
    npb = repo_data / "npb_2026-04-20.csv"
    assert kbo.exists()
    assert npb.exists()
    kbo_source = ManualCsvSource(str(kbo))
    npb_source = ManualCsvSource(str(npb))
    assert len(kbo_source.get_raw_games()) >= 1
    assert len(npb_source.get_raw_games()) >= 1
    for m in kbo_source.get_raw_markets():
        assert m["game_id"].startswith("KBO-")
    for m in npb_source.get_raw_markets():
        assert m["game_id"].startswith("NPB-")
