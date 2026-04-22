"""
Phase 21 CLI additions:
  - ledger, spotlight, overseas subcommands
  - --public-mode default ON, --no-public-mode opts out
  - Compliance gate blocks any --publish attempt whose preview card fails
    compliance_test(require_ledger_footer=True).
"""
import json
import pytest
from unittest.mock import patch

from edge_equation.__main__ import build_parser, main
from edge_equation.compliance.checker import ComplianceReport
from edge_equation.persistence.db import Database


def _run(argv, capsys):
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("EDGE_EQUATION_FAILSAFE_DIR", str(tmp_path / "failsafes"))
    for v in (
        "THE_ODDS_API_KEY", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
        "X_ACCESS_TOKEN_SECRET", "DISCORD_WEBHOOK_URL",
        "SMTP_HOST", "SMTP_FROM", "SMTP_TO", "EMAIL_TO",
    ):
        monkeypatch.delenv(v, raising=False)


def test_build_parser_has_phase21_subcommands():
    parser = build_parser()
    for cmd in ("ledger", "spotlight", "overseas"):
        args = parser.parse_args([cmd])
        assert args.subcommand == cmd


def test_public_mode_default_on():
    parser = build_parser()
    args = parser.parse_args(["daily"])
    assert args.public_mode is True


def test_no_public_mode_flag_opts_out():
    parser = build_parser()
    args = parser.parse_args(["daily", "--no-public-mode"])
    assert args.public_mode is False


def test_ledger_cli_runs_and_emits_json(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "ledger.db")
    code, cap = _run([
        "ledger", "--db", db_path, "--leagues", "MLB", "--prefer-mock",
    ], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["card_type"] == "the_ledger"


def test_spotlight_cli_runs(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "spot.db")
    code, cap = _run([
        "spotlight", "--db", db_path, "--leagues", "MLB", "--prefer-mock",
    ], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["card_type"] == "spotlight"


def test_overseas_cli_defaults_to_overseas_leagues(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "over.db")
    code, cap = _run([
        # Restrict to KBO/NPB since only those have mock sources.
        "overseas", "--db", db_path, "--leagues", "KBO,NPB", "--prefer-mock",
    ], capsys)
    assert code == 0
    payload = json.loads(cap.out)
    assert payload["card_type"] == "overseas_edge"


def test_compliance_gate_blocks_failing_card(tmp_path, monkeypatch, capsys):
    """If compliance_test reports a violation, the CLI must exit 3 and
    refuse to publish -- no call to the engine at all."""
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "block.db")

    blocked = ComplianceReport(ok=False, violations=["forbidden term: 'lock'"])
    with patch("edge_equation.__main__.compliance_test", return_value=blocked):
        code, cap = _run([
            "daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock", "--publish",
        ], capsys)
    assert code == 3
    assert "compliance_gate" in cap.err
    assert "blocked" in cap.err


def test_no_publish_skips_compliance_gate(tmp_path, monkeypatch, capsys):
    """Dry-path runs (no --publish) don't need the gate."""
    _isolate(monkeypatch, tmp_path)
    db_path = str(tmp_path / "dry.db")
    blocked = ComplianceReport(ok=False, violations=["anything"])
    # Even with a failing mock, --no-publish skips the gate entirely.
    with patch("edge_equation.__main__.compliance_test", return_value=blocked):
        code, _ = _run([
            "daily", "--db", db_path, "--leagues", "MLB",
            "--prefer-mock",  # default: no publish
        ], capsys)
    assert code == 0
