"""
Tests for scripts/bootstrap_mlb_xstats.py.

History note: the first attempt at this script scraped Baseball Savant
CSVs. It silently produced a 337-byte JSON with one entry keyed on
"2025" (the year, not a player ID) and obviously-broken xwoba values
(0.019, 0.025). These tests pin against that regression: any future
bootstrap that produces year-keyed entries or implausible xwoba ranges
fails loud.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Load the script as a module — it lives under scripts/ which isn't on
# the package path. importlib lets us pull it in for testing without
# making it an importable package.
def _load_script():
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "bootstrap_mlb_xstats.py"
    spec = importlib.util.spec_from_file_location("bootstrap_mlb_xstats", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_mlb_xstats"] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_script()


# ---------------- FIP→xwOBA proxy --------------------------------------

def test_fip_to_xwoba_at_league_avg_returns_league_xwoba():
    assert bootstrap.fip_to_xwoba(bootstrap.LEAGUE_FIP) == bootstrap.LEAGUE_XWOBA


def test_fip_to_xwoba_better_pitcher_lower_xwoba():
    bad = bootstrap.fip_to_xwoba(5.50)
    avg = bootstrap.fip_to_xwoba(4.20)
    good = bootstrap.fip_to_xwoba(2.50)
    assert good < avg < bad


def test_fip_to_xwoba_bounded():
    """Extreme FIPs must clamp into [0.250, 0.380]."""
    assert bootstrap.fip_to_xwoba(0.10) >= bootstrap.XWOBA_MIN
    assert bootstrap.fip_to_xwoba(15.0) <= bootstrap.XWOBA_MAX


# ---------------- compute_fip / IP parsing -----------------------------

def test_compute_fip_standard_pitcher():
    # 200 IP, 22 HR, 60 BB, 5 HBP, 220 K
    fip = bootstrap.compute_fip(hr=22, bb=60, hbp=5, k=220, ip=200.0)
    # = (13*22 + 3*65 - 2*220) / 200 + 3.10 = (286+195-440)/200 + 3.10
    # = 41/200 + 3.10 = 0.205 + 3.10 = 3.305
    assert fip is not None
    assert 3.20 < fip < 3.40


def test_compute_fip_missing_components_returns_none():
    assert bootstrap.compute_fip(None, 0, 0, 0, 100) is None
    assert bootstrap.compute_fip(0, 0, 0, 0, None) is None
    assert bootstrap.compute_fip(0, 0, 0, 0, 0.5) is None  # IP < 1


def test_ip_to_float_handles_thirds_format():
    """MLB API returns IP as e.g. '78.1' meaning 78 + 1/3."""
    assert abs(bootstrap._ip_to_float("78.1") - (78 + 1 / 3)) < 1e-9
    assert abs(bootstrap._ip_to_float("78.2") - (78 + 2 / 3)) < 1e-9
    assert bootstrap._ip_to_float("78.0") == 78.0
    assert bootstrap._ip_to_float(None) == 0.0
    assert bootstrap._ip_to_float("") == 0.0


# ---------------- fetch_pitching_leaders pagination --------------------

def _fake_stats_response(rows):
    return {
        "stats": [{
            "splits": [
                {
                    "player": {"id": r["id"], "fullName": r.get("name", "")},
                    "stat": {
                        "inningsPitched": r.get("ip", "0.0"),
                        "era": r.get("era"),
                        "whip": r.get("whip"),
                        "homeRuns": r.get("hr"),
                        "baseOnBalls": r.get("bb"),
                        "hitByPitch": r.get("hbp"),
                        "strikeOuts": r.get("k"),
                        "battersFaced": r.get("bf"),
                    },
                }
                for r in rows
            ],
        }],
    }


def test_fetch_pitching_leaders_paginates_until_short_page(monkeypatch):
    """fetch should call the API repeatedly until it gets a short page."""
    page_1 = [{"id": i, "ip": "150.0", "hr": 10, "bb": 30, "hbp": 2, "k": 130, "bf": 600} for i in range(250)]
    page_2 = [{"id": i, "ip": "150.0", "hr": 10, "bb": 30, "hbp": 2, "k": 130, "bf": 600} for i in range(250, 320)]

    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        offset = int(url.split("offset=")[-1])
        rows = page_1 if offset == 0 else page_2
        m = MagicMock()
        m.json.return_value = _fake_stats_response(rows)
        m.raise_for_status.return_value = None
        return m

    rows = bootstrap.fetch_pitching_leaders(2025, http_get=fake_get)
    assert len(rows) == 320
    assert len(calls) == 2  # stopped after short page


# ---------------- build_pitching_xstats end-to-end ---------------------

def test_build_pitching_xstats_keys_are_player_ids_not_year():
    """The bug the original Savant scraper hit: keys were '2025' instead
    of player IDs. This test explicitly forbids that."""
    rows = [
        {"id": 543037, "ip": 180.0, "hr": 18, "bb": 50, "hbp": 5, "k": 200, "bf": 720},
        {"id": 668678, "ip": 95.0,  "hr": 14, "bb": 40, "hbp": 3, "k": 100, "bf": 410},
    ]
    out = bootstrap.build_pitching_xstats(rows)
    assert set(out.keys()) == {"543037", "668678"}
    assert "2025" not in out
    for k, v in out.items():
        assert k.isdigit()
        assert 0.20 <= v["xwoba"] <= 0.40, v
        assert v["pa"] > 0


def test_build_pitching_xstats_xwoba_in_plausible_range():
    """Every emitted xwoba must be in [0.250, 0.380] — the bounds the
    fip_to_xwoba helper enforces. The original Savant bug emitted 0.019."""
    rows = []
    for i, fip_target in enumerate([2.0, 3.0, 4.2, 5.5, 7.0]):
        # Reverse-engineer HR/K to hit a target FIP at IP=150
        ip = 150.0
        # Using FIP = (13*HR + 3*BB - 2*K)/IP + 3.1 with rough constants:
        rows.append({
            "id": 1000 + i, "ip": ip,
            "hr": 15, "bb": 50, "hbp": 5, "k": 130 + int(20 * (4.2 - fip_target)),
            "bf": 600,
        })
    out = bootstrap.build_pitching_xstats(rows)
    for v in out.values():
        assert 0.20 <= v["xwoba"] <= 0.40


def test_build_pitching_xstats_skips_rows_with_no_id():
    rows = [
        {"id": None, "ip": 100.0, "hr": 10, "bb": 30, "hbp": 2, "k": 100, "bf": 400},
        {"id": 543, "ip": 100.0, "hr": 10, "bb": 30, "hbp": 2, "k": 100, "bf": 400},
    ]
    out = bootstrap.build_pitching_xstats(rows)
    assert "543" in out
    assert len(out) == 1


# ---------------- validate_pitching ------------------------------------

def test_validate_pitching_passes_on_healthy_payload():
    payload = {
        "season": 2025,
        "pitching": {
            str(1000 + i): {"pa": 200, "xwoba": 0.310, "xba": 0.250, "xslg": 0.400}
            for i in range(60)
        },
    }
    assert bootstrap.validate_pitching(payload, 2025) == []


def test_validate_pitching_rejects_too_few_pitchers():
    payload = {
        "season": 2025,
        "pitching": {
            str(1000 + i): {"pa": 200, "xwoba": 0.310, "xba": 0.250, "xslg": 0.400}
            for i in range(10)
        },
    }
    errors = bootstrap.validate_pitching(payload, 2025)
    assert any("pitchers" in e and "10" in e for e in errors)


def test_validate_pitching_rejects_year_keyed_entry():
    """Pin the original Savant bug: refuse to write a file where the
    player_id is the year."""
    payload = {
        "season": 2025,
        "pitching": {
            "2025": {"pa": 149, "xwoba": 0.019, "xba": 0.025, "xslg": 0.040},
        },
    }
    errors = bootstrap.validate_pitching(payload, 2025)
    assert any("season" in e for e in errors)


def test_validate_pitching_rejects_implausible_xwoba():
    payload = {
        "season": 2025,
        "pitching": {
            **{str(1000 + i): {"pa": 200, "xwoba": 0.310, "xba": 0.250, "xslg": 0.400}
               for i in range(60)},
            "9999": {"pa": 200, "xwoba": 0.019, "xba": 0.025, "xslg": 0.040},
        },
    }
    errors = bootstrap.validate_pitching(payload, 2025)
    assert any("0.019" in e or "implausible" in e for e in errors)


def test_validate_pitching_rejects_non_numeric_key():
    payload = {
        "season": 2025,
        "pitching": {
            "abc": {"pa": 200, "xwoba": 0.310, "xba": 0.250, "xslg": 0.400},
        },
    }
    errors = bootstrap.validate_pitching(payload, 2025)
    assert any("non-numeric" in e for e in errors)


# ---------------- end-to-end bootstrap_season --------------------------

def test_bootstrap_season_writes_valid_file(tmp_path: Path):
    rows = [
        {"id": 1000 + i, "ip": "150.0", "hr": 15, "bb": 50, "hbp": 5,
         "k": 150, "bf": 600}
        for i in range(80)
    ]

    def fake_get(url, timeout=None):
        offset = int(url.split("offset=")[-1])
        # Single page worth (250 limit, only 80 rows → short page)
        m = MagicMock()
        m.json.return_value = _fake_stats_response(rows if offset == 0 else [])
        m.raise_for_status.return_value = None
        return m

    payload = bootstrap.bootstrap_season(
        2025, tmp_path, http_get=fake_get,
    )
    out_file = tmp_path / "2025" / "statcast_xstats.json"
    assert out_file.exists()
    on_disk = json.loads(out_file.read_text())
    assert on_disk["season"] == 2025
    assert "fip_proxy" in on_disk["source"]
    assert len(on_disk["pitching"]) == 80
    # Pin the original-bug regression:
    assert "2025" not in on_disk["pitching"]
    for k, v in on_disk["pitching"].items():
        assert k.isdigit()
        assert 0.20 <= v["xwoba"] <= 0.40
