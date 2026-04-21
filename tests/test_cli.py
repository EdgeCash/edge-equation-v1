import io
import json
import sys
from decimal import Decimal
from pathlib import Path
import pytest

from edge_equation.__main__ import build_parser, main
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.realization_store import RealizationStore
from edge_equation.persistence.slate_store import SlateStore


def _run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured


def _isolate(monkeypatch, tmp_path):
    """Force the default failsafe to tmp_path and strip real-service env vars."""
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    for v in ("THE_ODDS_API_KEY", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
              "X_ACCESS_TOKEN_SECRET", "DISCORD_WEBHOOK_URL",
              "SMTP_HOST", "SMTP_FROM", "SMTP_TO", "EMAIL_TO"):
        monkeypatch.delenv(v, raising=False)


# ---------------------------------------------------- parser


def test_build_parser_has_subcommands():
    parser = build_parser()
    # Crude: parse each subcommand with --help removed to verify they exist
    for cmd in ("daily", "evening", "settle", "pipeline"):
        args = parser.parse_args([cmd, "outcomes.csv"] if cmd == "settle" else [cmd])
        assert args.subcommand == cmd


def test_slate_flags_default_to_safe_values():
    parser = build_parser()
    args = parser.parse_args(["daily"])
    assert args.publish is False
    assert args.dry_run is True
    assert args.prefer_mock is False


def test_publish_flag_sets_publish_true():
    parser = build_parser()
    args = parser.parse_args(["daily", "--publish"])
    assert args.publish is True


def test_no_dry_run_flag_opts_out():
    parser = build_parser()
    args = parser.parse_args(["daily", "--no-dry-run"])
    assert args.dry_run is False


# ----------------------------------------------------- daily / evening


def test_daily_dry_run_outputs_json_summary(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "test.db")
    code, cap = _run([
        "daily", "--db", db_path, "--leagues", "MLB",
        "--prefer-mock", "--publish",
    ], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["slate_id"].startswith("daily_edge_")
    assert payload["slate_id"].endswith("_mlb")
    assert payload["card_type"] == "daily_edge"
    assert payload["new_slate"] is True
    assert payload["n_picks"] > 0
    assert len(payload["publish_results"]) == 3


def test_evening_single_league(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "test.db")
    code, cap = _run([
        "evening", "--db", db_path, "--leagues", "NHL", "--prefer-mock",
    ], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["card_type"] == "evening_edge"
    assert payload["n_picks"] >= 0
    assert "nhl" in payload["slate_id"]


def test_daily_twice_is_idempotent(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "test.db")
    argv = ["daily", "--db", db_path, "--leagues", "MLB", "--prefer-mock"]
    _run(argv, capsys)  # first run
    _, cap_second = _run(argv, capsys)  # second run, captured
    payload = json.loads(cap_second.out)
    assert payload["new_slate"] is False


def test_daily_multi_league(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "test.db")
    code, cap = _run([
        "daily", "--db", db_path, "--leagues", "MLB,NHL", "--prefer-mock",
    ], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert "mlb" not in payload["slate_id"]  # multi-league gets no sport suffix
    assert payload["n_picks"] > 0


def test_daily_publish_with_failsafe_still_exits_zero(tmp_path, monkeypatch, capsys):
    # All publishers fail (no creds) but each triggers its failsafe ->
    # CLI still exits 0 because failsafe_triggered.
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "test.db")
    code, cap = _run([
        "daily", "--db", db_path, "--leagues", "MLB",
        "--prefer-mock", "--publish", "--no-dry-run",
    ], capsys)
    payload = json.loads(cap.out)
    for r in payload["publish_results"]:
        assert r["success"] is False
        assert r["failsafe_triggered"] is True
    assert code == 0  # failsafe absorbed the failure


# ----------------------------------------------------- settle


def _seed_slate_with_pick(conn, slate_id="s1"):
    from edge_equation.engine.pick_schema import Line, Pick
    from edge_equation.persistence.slate_store import SlateRecord

    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id, generated_at="2026-04-20T09:00",
        sport="MLB", card_type="daily_edge",
    ))
    PickStore.insert(conn, Pick(
        sport="MLB", market_type="ML", selection="BOS",
        line=Line(odds=-132), fair_prob=Decimal('0.55'),
        edge=Decimal('0.02'), kelly=Decimal('0.008'),
        grade="B", realization=52, game_id="G1",
    ), slate_id=slate_id)


def test_settle_records_outcomes_from_csv(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = Database.open(db_path)
    Database.migrate(conn)
    _seed_slate_with_pick(conn)
    conn.close()

    csv_path = tmp_path / "outcomes.csv"
    csv_path.write_text(
        "game_id,market_type,selection,outcome,actual_value\n"
        "G1,ML,BOS,win,\n",
        encoding="utf-8",
    )
    code, cap = _run(["settle", str(csv_path), "--db", db_path], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["recorded_outcomes"] == 1
    assert payload["matched"] == 1
    assert payload["updated"] == 1

    # Verify realization field was updated
    conn = Database.open(db_path)
    picks = PickStore.list_by_game(conn, "G1")
    assert picks[0].realization == 100
    conn.close()


def test_settle_missing_columns_exits_two(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = Database.open(db_path)
    Database.migrate(conn)
    conn.close()

    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("game_id,market_type\nG1,ML\n", encoding="utf-8")
    code, cap = _run(["settle", str(bad_csv), "--db", db_path], capsys)
    assert code == 2
    assert "missing columns" in cap.err


def test_settle_records_with_actual_value(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = Database.open(db_path)
    Database.migrate(conn)
    _seed_slate_with_pick(conn)
    conn.close()

    csv_path = tmp_path / "outcomes.csv"
    csv_path.write_text(
        "game_id,market_type,selection,outcome,actual_value\n"
        "G1,Total,Over 9.5,win,11\n",
        encoding="utf-8",
    )
    _run(["settle", str(csv_path), "--db", db_path], capsys)

    conn = Database.open(db_path)
    outcome = RealizationStore.get_outcome(conn, "G1", "Total", "Over 9.5")
    assert outcome is not None
    assert outcome.actual_value == Decimal('11')
    conn.close()


# ----------------------------------------------------- no-subcommand compat


def test_no_subcommand_runs_legacy_pipeline(capsys):
    # The Phase-1 pipeline still works via `python -m edge_equation`.
    code, cap = _run([], capsys)
    assert code == 0
    # The pipeline prints a dict with status=ok
    assert "ok" in cap.out or "status" in cap.out
