"""
Phase 14 end-to-end integration:

Before Phase 14, CSV-only slates (KBO/NPB) persisted a slate but produced
zero picks because the CSV rows lack engine feature inputs. With Phase 14,
a separate game-results feed (also CSV) fills the Elo + rolling-stats
tables, and the scheduled runner's _collect_slate step now injects
FeatureComposer-derived inputs into every market that doesn't already
have them.

These tests prove the complete flow works:

  results CSV  -> GameResultsStore        (Elo history)
  odds CSV     -> ManualCsvSource         (upcoming markets)
  scheduler    -> enrich_markets          (feature inputs filled in)
  engine       -> Picks                   (now populated, not zero!)
"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import pytest

from edge_equation.engine.scheduled_runner import (
    CARD_TYPE_DAILY,
    CARD_TYPE_OVERSEAS_EDGE,
    ScheduledRunner,
)
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.stats.csv_loader import ResultsCsvLoader
from edge_equation.stats.results import GameResult, GameResultsStore


RUN_DT = datetime(2026, 4, 20, 9, 0, 0)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _seed_kbo_results(conn, n_weeks: int = 3):
    """Generate enough KBO history that Elo and shrinkage both settle down."""
    teams = ("Doosan Bears", "LG Twins", "KIA Tigers", "SSG Landers")
    day = 0
    i = 0
    for week in range(n_weeks):
        for j in range(len(teams)):
            home = teams[j]
            away = teams[(j + 1) % len(teams)]
            # Doosan slightly stronger -> wins more often, scores more
            hs = 6 if home == "Doosan Bears" else 4
            as_ = 3 if away != "Doosan Bears" else 5
            ts = f"2026-03-{1 + day:02d}T18:30:00+09:00"
            GameResultsStore.record(conn, GameResult(
                result_id=None, game_id=f"KBO-SEED-{i}",
                league="KBO",
                home_team=home, away_team=away,
                start_time=ts,
                home_score=hs, away_score=as_,
                status="final",
            ))
            day += 1
            i += 1


def _write_kbo_slate_csv(tmp_path):
    body = (
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,Doosan Bears,,-140\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,LG Twins,,+120\n"
        "KBO,KBO-2026-04-20-LG-DB,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,Total,Over 9.5,9.5,-115\n"
    )
    path = tmp_path / "kbo_2026-04-20.csv"
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------------------ happy path


def test_csv_slate_with_results_history_produces_picks(conn, tmp_path):
    _seed_kbo_results(conn)
    _write_kbo_slate_csv(tmp_path)

    # Phase 24 slate separation: KBO is overseas-only; run it through
    # CARD_TYPE_OVERSEAS_EDGE so the runner's off-slate filter doesn't
    # strip the whole league.
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["KBO"],
        csv_dir=str(tmp_path),
        prefer_mock=False,  # use the CSV slate
    )
    assert summary.new_slate is True
    assert summary.n_games == 1
    # The Phase 14 win: CSV-only slate now produces picks because the composer
    # filled in strength + totals inputs from history.
    assert summary.n_picks > 0

    picks = PickStore.list_by_slate(conn, summary.slate_id)
    sports = {p.sport for p in picks}
    assert "KBO" in sports


def test_csv_slate_without_results_history_still_persists_slate(conn, tmp_path):
    # No result history in the DB -> enrich_markets is a no-op and the CSV
    # markets have no meta.inputs, so picks stay at 0 just like pre-Phase-14.
    _write_kbo_slate_csv(tmp_path)

    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE,
        conn=conn,
        run_datetime=RUN_DT,
        leagues=["KBO"],
        csv_dir=str(tmp_path),
        prefer_mock=False,
    )
    assert summary.new_slate is True
    assert summary.n_games == 1
    assert summary.n_picks == 0  # expected: nothing to compose from


def test_stronger_home_team_gets_home_prob_above_half(conn, tmp_path):
    # Engine's ML fair_prob is always the HOME team's win probability, so
    # with Doosan (stronger) at home we expect fair_prob > 0.5.
    _seed_kbo_results(conn, n_weeks=5)  # generous history for Elo to converge
    _write_kbo_slate_csv(tmp_path)

    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE, conn=conn, run_datetime=RUN_DT,
        leagues=["KBO"], csv_dir=str(tmp_path), prefer_mock=False,
    )
    picks = PickStore.list_by_slate(conn, summary.slate_id)
    ml_picks = [p for p in picks if p.market_type == "ML"]
    assert ml_picks, "expected at least one ML pick"
    # Phase 28: pick.fair_prob is now the SELECTION's win probability
    # (the engine flips the home-centric prob for away selections).
    # Doosan is the stronger home team here, so the Doosan pick's
    # fair_prob > 0.5 and the LG (away) pick's fair_prob < 0.5.
    home_picks = [p for p in ml_picks if p.selection == "Doosan Bears"]
    away_picks = [p for p in ml_picks if p.selection == "LG Twins"]
    assert home_picks, "expected the home (Doosan) ML pick to be present"
    assert all(p.fair_prob > Decimal("0.5") for p in home_picks)
    if away_picks:
        assert all(p.fair_prob < Decimal("0.5") for p in away_picks), (
            "away (LG) pick must carry the COMPLEMENT of home win prob"
        )


# --------------------------------------------------- shipped sample CSV


def test_shipped_kbo_results_csv_parses_and_is_usable(conn, tmp_path):
    # Load the sample KBO results CSV that ships in data/.
    sample = Path(__file__).resolve().parent.parent / "data" / "kbo_results_2026-04-13_to_2026-04-19.csv"
    ResultsCsvLoader.load_file(conn, str(sample))
    assert GameResultsStore.count_by_league(conn, "KBO") >= 5

    # Feed the upcoming-games CSV too.
    _write_kbo_slate_csv(tmp_path)
    summary = ScheduledRunner.run(
        card_type=CARD_TYPE_OVERSEAS_EDGE, conn=conn, run_datetime=RUN_DT,
        leagues=["KBO"], csv_dir=str(tmp_path), prefer_mock=False,
    )
    # With ~10 games of history per team, the composer produces features
    # even if shrinkage leaves them modest.
    assert summary.n_games == 1
    # At least one pick should be generated now that the composer has data.
    assert summary.n_picks >= 1


# --------------------------------------------------- mocked sources unaffected


def test_mock_sources_not_clobbered_by_composer(conn):
    # Mock sources emit meta.inputs already; the composer must NOT overwrite
    # them. Compare pick output with and without result history.
    # (run #1: no results in DB)
    summary_no_results = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    n_picks_no_results = summary_no_results.n_picks
    assert n_picks_no_results > 0

    # Clear slate + picks to allow a second run with results seeded.
    conn.execute("DELETE FROM picks")
    conn.execute("DELETE FROM slates")
    conn.commit()
    GameResultsStore.record(conn, GameResult(
        result_id=None, game_id="MLB-seed-1", league="MLB",
        home_team="BOS", away_team="DET",
        start_time="2026-04-15T13:05:00-04:00",
        home_score=8, away_score=2, status="final",
    ))

    summary_with_results = ScheduledRunner.run(
        card_type=CARD_TYPE_DAILY, conn=conn, run_datetime=RUN_DT,
        leagues=["MLB"], prefer_mock=True,
    )
    # Same number of picks -- the composer didn't disturb the mock's inputs.
    assert summary_with_results.n_picks == n_picks_no_results


# --------------------------------------------------- CLI smoke


def test_cli_load_results_and_run(tmp_path, monkeypatch, capsys):
    # End-to-end CLI flow: load-results -> daily.
    from edge_equation.__main__ import main
    db_path = str(tmp_path / "phase14.db")
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))

    results_csv = tmp_path / "kbo_results.csv"
    results_csv.write_text(
        "league,game_id,start_time,home_team,away_team,home_score,away_score,status\n"
        "KBO,SEED-1,2026-04-01T18:30:00+09:00,Doosan Bears,LG Twins,6,3,final\n"
        "KBO,SEED-2,2026-04-02T18:30:00+09:00,LG Twins,Doosan Bears,2,5,final\n"
        "KBO,SEED-3,2026-04-03T18:30:00+09:00,Doosan Bears,LG Twins,7,4,final\n",
        encoding="utf-8",
    )
    slate_csv = tmp_path / "kbo_2026-04-20.csv"
    slate_csv.write_text(
        "league,game_id,start_time,home_team,away_team,market_type,selection,line,odds\n"
        "KBO,UPCOMING,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,Doosan Bears,,-140\n"
        "KBO,UPCOMING,2026-04-20T18:30:00+09:00,Doosan Bears,LG Twins,ML,LG Twins,,+120\n",
        encoding="utf-8",
    )

    assert main(["load-results", str(results_csv), "--db", db_path]) == 0
    capsys.readouterr()
    # Phase 24 slate separation: KBO ingestion runs through overseas.
    assert main([
        "overseas", "--db", db_path, "--leagues", "KBO",
        "--csv-dir", str(tmp_path),
    ]) == 0
    out = capsys.readouterr().out
    import json
    payload = json.loads(out)
    # With the seeded history the composer can fill inputs, so picks > 0.
    assert payload["n_picks"] > 0
