"""Tests for the player-profile writers.

Covers the contract every per-sport writer must honour so the
website's `lib/player-data.ts` loader (and the GameLogTable +
TodaysContextPanel components) can read the JSON without
fabrication.

What we lock down:

  - Slug rule matches the website's `slugify` (alphanumeric,
    hyphen-separated, lowercase, no leading/trailing hyphens).
  - Atomic writer drops a file at exactly
    `<sport>/{player_logs,context_today}/<slug>.json`.
  - `GameLog.to_dict()` produces a `{player, rows: [{date, opponent,
    is_home, result, stats}]}` shape — same fields the loader reads.
  - `TodaysContext.to_dict()` produces a `{player, as_of, items:
    [{label, value}]}` shape.
  - The slate-walking helper extracts player names from the daily
    feed's PLAYER_PROP_* selections only — game-result rows are
    ignored.
  - Stub writers (WNBA, football) emit a "Limited data" stub the
    website renders as the honest empty state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from edge_equation.exporters.player_profiles import (
    build_for_slate,
    players_from_slate_picks,
    slugify,
    write_player_log,
    write_today_context,
)
from edge_equation.exporters.player_profiles.writer import (
    ContextItem,
    GameLog,
    GameLogRow,
    TodaysContext,
    empty_context,
    empty_log,
    output_dirs_for_sport,
)
from edge_equation.exporters.player_profiles.football import (
    write_profiles_for_slate as football_write,
)
from edge_equation.exporters.player_profiles.wnba import (
    write_profiles_for_slate as wnba_write,
)


# ---------------------------------------------------------------------------
# Slug rule — must match web/lib/search-index.ts:slugify exactly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Aaron Judge",          "aaron-judge"),
        ("A'ja Wilson",          "a-ja-wilson"),
        ("Patrick Mahomes",      "patrick-mahomes"),
        ("CeeDee Lamb",          "ceedee-lamb"),
        ("  Cal Raleigh  ",      "cal-raleigh"),       # trim
        ("José Ramírez",         "jos-ram-rez"),       # accented strip
        ("Player.Name-Junior",   "player-name-junior"),
        ("JaMarr Chase",         "jamarr-chase"),
        ("",                     ""),
    ],
)
def test_slugify_matches_website_rules(name: str, expected: str):
    assert slugify(name) == expected


# ---------------------------------------------------------------------------
# JSON shape — locked to what `web/lib/player-data.ts` reads.
# ---------------------------------------------------------------------------


def test_game_log_to_dict_shape():
    log = GameLog(
        player="Aaron Judge",
        rows=[
            GameLogRow(
                date="2026-04-29", opponent="BOS", is_home=True,
                result="W",
                stats={"AB": 4, "H": 2, "HR": 1, "RBI": 3, "BB": 1, "K": 1},
            ),
        ],
    )
    d = log.to_dict()
    assert d["player"] == "Aaron Judge"
    assert isinstance(d["rows"], list)
    row = d["rows"][0]
    assert set(row.keys()) >= {"date", "opponent", "is_home", "result", "stats"}
    assert row["stats"]["HR"] == 1


def test_todays_context_to_dict_shape():
    ctx = TodaysContext(
        player="Aaron Judge",
        items=[
            ContextItem(label="Lineup spot", value="2nd, DH"),
            ContextItem(label="Opponent", value="vs BOS"),
        ],
    )
    d = ctx.to_dict()
    assert d["player"] == "Aaron Judge"
    assert isinstance(d["items"], list)
    assert d["items"][0] == {"label": "Lineup spot", "value": "2nd, DH"}
    # `as_of` is auto-populated.
    assert isinstance(d["as_of"], str) and len(d["as_of"]) > 0


# ---------------------------------------------------------------------------
# Atomic writer — file lands at the right path.
# ---------------------------------------------------------------------------


def test_write_player_log_lands_at_correct_path(tmp_path: Path):
    log = GameLog(player="Aaron Judge", rows=[])
    written = write_player_log("mlb", log, out_root=tmp_path)
    assert written == tmp_path / "mlb" / "player_logs" / "aaron-judge.json"
    assert written.exists()
    parsed = json.loads(written.read_text())
    assert parsed["player"] == "Aaron Judge"
    assert parsed["rows"] == []


def test_write_today_context_lands_at_correct_path(tmp_path: Path):
    ctx = TodaysContext(
        player="A'ja Wilson",
        items=[ContextItem(label="Slate date", value="2026-05-09")],
    )
    written = write_today_context("wnba", ctx, out_root=tmp_path)
    assert (
        written
        == tmp_path / "wnba" / "context_today" / "a-ja-wilson.json"
    )
    parsed = json.loads(written.read_text())
    assert parsed["player"] == "A'ja Wilson"
    assert parsed["items"][0]["value"] == "2026-05-09"


def test_write_refuses_empty_slug(tmp_path: Path):
    with pytest.raises(ValueError):
        write_player_log("mlb", GameLog(player="", rows=[]), out_root=tmp_path)
    with pytest.raises(ValueError):
        write_today_context(
            "mlb", TodaysContext(player="", items=[]), out_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# Slate walker — extracts player names from PLAYER_PROP_* rows only.
# ---------------------------------------------------------------------------


def _pick(market: str, selection: str) -> dict[str, Any]:
    return {"market_type": market, "selection": selection}


def test_players_from_slate_picks_pulls_player_props_only():
    picks = [
        _pick("PLAYER_PROP_HR",       "Aaron Judge · Home Runs Over 0.5"),
        _pick("PLAYER_PROP_PASS_YDS", "Patrick Mahomes · Passing Yards Over 275.5"),
        _pick("MONEYLINE",            "NYY · Moneyline"),
        _pick("RUN_LINE",             "BOS · Run Line +1.5"),
    ]
    names = players_from_slate_picks(picks)
    assert "Aaron Judge" in names
    assert "Patrick Mahomes" in names
    assert all("Moneyline" not in n for n in names)
    # De-duped — running again with a duplicate still returns one entry.
    names2 = players_from_slate_picks(picks + [picks[0]])
    assert names2 == names


def test_players_from_slate_picks_skips_empty_or_malformed():
    picks = [
        _pick("PLAYER_PROP_HR", ""),                    # empty selection
        _pick("PLAYER_PROP_HR", "  ·  "),                # only separators
        _pick("PLAYER_PROP_HR", "Aaron Judge · HR"),
    ]
    names = players_from_slate_picks(picks)
    assert names == ["Aaron Judge"]


# ---------------------------------------------------------------------------
# Stub writers — emit the "Limited data" shape the website renders.
# ---------------------------------------------------------------------------


def test_wnba_stub_writer_emits_limited_data(tmp_path: Path):
    counts = wnba_write(
        target_date="2026-05-09",
        player_names=["A'ja Wilson", "Sabrina Ionescu"],
        out_root=tmp_path,
    )
    assert counts == {"logs": 2, "context": 2}
    log_path = tmp_path / "wnba" / "player_logs" / "a-ja-wilson.json"
    assert log_path.exists()
    log = json.loads(log_path.read_text())
    assert log["player"] == "A'ja Wilson"
    assert log["rows"] == []           # honest empty state

    ctx_path = tmp_path / "wnba" / "context_today" / "a-ja-wilson.json"
    ctx = json.loads(ctx_path.read_text())
    assert ctx["player"] == "A'ja Wilson"
    labels = [item["label"] for item in ctx["items"]]
    assert "Source" in labels
    source_value = next(
        item["value"] for item in ctx["items"] if item["label"] == "Source"
    )
    assert "Limited data" in source_value


def test_football_stub_writer_routes_per_sport(tmp_path: Path):
    nfl_counts = football_write(
        sport="nfl",
        target_date="2026-09-04",
        player_names=["Patrick Mahomes"],
        out_root=tmp_path,
    )
    ncaaf_counts = football_write(
        sport="ncaaf",
        target_date="2026-09-05",
        player_names=["Quinn Ewers"],
        out_root=tmp_path,
    )
    assert nfl_counts == {"logs": 1, "context": 1}
    assert ncaaf_counts == {"logs": 1, "context": 1}
    assert (
        tmp_path / "nfl" / "player_logs" / "patrick-mahomes.json"
    ).exists()
    assert (
        tmp_path / "ncaaf" / "context_today" / "quinn-ewers.json"
    ).exists()


def test_football_writer_rejects_unsupported_sport(tmp_path: Path):
    with pytest.raises(ValueError):
        football_write(
            sport="hockey",
            target_date="2026-01-01",
            player_names=["Player"],
            out_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# build_for_slate — the orchestrator the master daily runner calls.
# ---------------------------------------------------------------------------


def test_build_for_slate_walks_unified_feed(tmp_path: Path, monkeypatch):
    """Synthetic unified feed → writers fire for every sport's prop
    selections."""
    feed = {
        "picks": [
            # MLB
            _pick("PLAYER_PROP_HR", "Aaron Judge · Home Runs Over 0.5"),
            _pick("PLAYER_PROP_K",  "Cole · Strikeouts Over 6.5"),
        ],
        "wnba": {
            "picks": [
                _pick(
                    "PLAYER_PROP_POINTS",
                    "A'ja Wilson · Points Over 22.5",
                ),
            ],
        },
        "nfl": {
            "picks": [
                _pick(
                    "PLAYER_PROP_PASS_YDS",
                    "Patrick Mahomes · Passing Yards Over 275.5",
                ),
            ],
        },
        "ncaaf": {
            "picks": [
                _pick(
                    "PLAYER_PROP_RUSH_YDS",
                    "Quinn Ewers · Rushing Yards Over 35.5",
                ),
            ],
        },
    }

    # Patch the MLB writer to skip the live Statcast pull (which
    # requires pybaseball + network) and write empty logs instead.
    # We're testing the orchestrator wiring, not Statcast.
    from edge_equation.exporters.player_profiles import mlb as mlb_mod
    def _stub_mlb_writer(
        *, target_date=None, player_names, role="batter",
        days=60, config=None, out_root=None,
    ):
        from edge_equation.exporters.player_profiles.writer import (
            empty_log, empty_context, write_player_log, write_today_context,
            ContextItem, TodaysContext,
        )
        n = 0
        for name in player_names:
            if not name:
                continue
            write_player_log("mlb", empty_log(name), out_root=out_root)
            write_today_context(
                "mlb",
                TodaysContext(
                    player=name,
                    items=[ContextItem(label="Slate role", value=role)],
                ),
                out_root=out_root,
            )
            n += 1
        return {"logs": n, "context": n}
    monkeypatch.setattr(mlb_mod, "write_profiles_for_slate", _stub_mlb_writer)

    summary = build_for_slate(
        feed,
        target_date="2026-05-09",
        out_root=tmp_path,
    )
    assert summary["mlb"]["logs"] >= 1
    assert summary["wnba"]["logs"] == 1
    assert summary["nfl"]["logs"] == 1
    assert summary["ncaaf"]["logs"] == 1

    # Each sport produced files at the right path.
    assert (tmp_path / "wnba" / "player_logs" / "a-ja-wilson.json").exists()
    assert (
        tmp_path / "nfl" / "player_logs" / "patrick-mahomes.json"
    ).exists()
    assert (
        tmp_path / "ncaaf" / "context_today" / "quinn-ewers.json"
    ).exists()


# ---------------------------------------------------------------------------
# Output-paths catalog — every sport's player-profile dirs flow
# through to the master workflow's commit step.
# ---------------------------------------------------------------------------


def test_output_dirs_for_sport_returns_two_dirs():
    dirs = output_dirs_for_sport("mlb")
    assert dirs == [
        "website/public/data/mlb/player_logs/",
        "website/public/data/mlb/context_today/",
    ]


def test_master_runner_output_paths_include_player_dirs():
    """The master script's `--list-output-paths` must include the
    player_logs + context_today dirs for every sport in the catalog
    so the daily-master workflow auto-picks them up at commit time."""
    import os
    import subprocess
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(repo_root / "src")
        + os.pathsep + env.get("PYTHONPATH", "")
    )
    out = subprocess.run(
        ["python", "run_daily_all.py", "--list-output-paths"],
        cwd=repo_root, env=env, capture_output=True, text=True, check=True,
    )
    paths = set(line.strip() for line in out.stdout.splitlines() if line.strip())
    for sport in ["mlb", "wnba", "nfl", "ncaaf"]:
        assert (
            f"website/public/data/{sport}/player_logs/" in paths
        ), f"{sport} player_logs/ missing from output paths"
        assert (
            f"website/public/data/{sport}/context_today/" in paths
        ), f"{sport} context_today/ missing from output paths"
