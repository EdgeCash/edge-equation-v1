"""Smoke tests for the prop-specific backfill (PrizePicks snapshots
+ synthetic outcomes).

Exercises the shape contract --- snapshot parsing, market_type
mapping, model_prob distributions per odds_type, deterministic
seeding, same-player correlation injection. Doesn't try to
re-validate the math beyond a handful of bounds-check assertions
since the synthetic model is the contract; the next iteration of
the backfill (real ledger) replaces it wholesale.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytest.importorskip("numpy")


def _write_snapshot(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    """Write a CSV snapshot with the PrizePicks header order this
    module expects."""
    f = tmp_path / name
    cols = [
        "scraped_at", "projection_id", "league", "sport", "player",
        "team", "position", "description", "stat_type", "line",
        "start_time", "odds_type",
    ]
    with f.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in cols})
    return f


def _row(
    pid: str = "1",
    *,
    player: str = "Aaron Judge",
    stat: str = "Hits",
    line: float = 0.5,
    odds_type: str = "demon",
    league: str = "MLB",
    desc: str = "BOS",
    date: str = "2026-05-01",
) -> dict:
    return {
        "scraped_at": f"{date}T18:00:00Z",
        "projection_id": pid,
        "league": league,
        "sport": "",
        "player": player,
        "team": "NYY",
        "position": "OF",
        "description": desc,
        "stat_type": stat,
        "line": str(line),
        "start_time": f"{date}T19:10:00.000-04:00",
        "odds_type": odds_type,
    }


# ---------------------------------------------------------------------------
# Snapshot loading + filtering
# ---------------------------------------------------------------------------


def test_load_props_backfill_groups_by_date(tmp_path):
    """Structural test: each snapshot date becomes one slate, regardless
    of tier. ``min_tier=NO_PLAY`` keeps every parsed leg so the test
    isn't subject to the synthetic distribution's tail behavior."""
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", [
        _row(pid="1", player="A", date="2026-05-01"),
        _row(pid="2", player="B", date="2026-05-01", odds_type="goblin"),
    ])
    _write_snapshot(tmp_path, "prizepicks_2026-05-02T18-00-00Z.csv", [
        _row(pid="3", player="C", date="2026-05-02"),
    ])
    src, slates = load_props_backfill(
        tmp_path, config=PropSyntheticConfig(max_legs_per_date=10),
        min_tier=Tier.NO_PLAY,
    )
    dates = {s.date for s in slates}
    assert dates == {"2026-05-01", "2026-05-02"}
    assert src.first_date == "2026-05-01"
    assert src.last_date == "2026-05-02"


def test_load_props_backfill_filters_to_mlb_only(tmp_path):
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", [
        _row(pid="1", player="A", league="MLB"),
        _row(pid="2", player="B", league="NBA"),
        _row(pid="3", player="C", league="MLBLIVE"),
    ])
    src, slates = load_props_backfill(
        tmp_path, config=PropSyntheticConfig(max_legs_per_date=10),
        min_tier=Tier.NO_PLAY,
    )
    assert len(slates) == 1
    assert all(g.leg.player_id == "prop:A" for g in slates[0].graded_legs)


def test_load_props_backfill_skips_unmapped_stat_types(tmp_path):
    """Composite stats like ``Hits+Runs+RBIs`` aren't in the parlay
    market universe and should be dropped at parse time."""
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", [
        _row(pid="1", player="A", stat="Hits"),
        _row(pid="2", player="B", stat="Hits+Runs+RBIs"),
        _row(pid="3", player="C", stat="Total Bases"),
    ])
    _, slates = load_props_backfill(
        tmp_path, config=PropSyntheticConfig(max_legs_per_date=10),
        min_tier=Tier.NO_PLAY,
    )
    markets = {g.leg.market_type for g in slates[0].graded_legs}
    assert "Hits" in markets
    assert "Total_Bases" in markets
    # Hits+Runs+RBIs has no mapping; should not appear.
    assert all(m in {"Hits", "Total_Bases"} for m in markets)


# ---------------------------------------------------------------------------
# Synthetic model contracts
# ---------------------------------------------------------------------------


def test_demon_model_prob_distribution_centers_on_demon_mean(tmp_path):
    """Big snapshot of demons should yield mean model_prob near 0.40
    when we read the *unfiltered* leg pool. The default LEAN tier
    filter only keeps the upper tail (positive-edge legs against the
    bucket's fair odds), so we have to bypass it to see the full
    distribution."""
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    rows = [
        _row(pid=str(i), player=f"Player{i % 8}", stat="Hits", odds_type="demon")
        for i in range(200)
    ]
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", rows)
    cfg = PropSyntheticConfig(max_legs_per_date=200)
    _, slates = load_props_backfill(tmp_path, config=cfg, min_tier=Tier.NO_PLAY)
    probs = [g.leg.side_probability for g in slates[0].graded_legs]
    assert len(probs) > 0
    mean = sum(probs) / len(probs)
    # Beta sample with mean=0.40, std=0.07 should land within ±0.05.
    assert abs(mean - cfg.demon_model_prob_mean) < 0.05


def test_goblin_model_prob_distribution_centers_on_goblin_mean(tmp_path):
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    rows = [
        _row(pid=str(i), player=f"Player{i % 8}", stat="Hits", odds_type="goblin")
        for i in range(200)
    ]
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", rows)
    cfg = PropSyntheticConfig(max_legs_per_date=200)
    _, slates = load_props_backfill(tmp_path, config=cfg, min_tier=Tier.NO_PLAY)
    probs = [g.leg.side_probability for g in slates[0].graded_legs]
    mean = sum(probs) / len(probs)
    assert abs(mean - cfg.goblin_model_prob_mean) < 0.05


def test_outcomes_correlated_within_player_within_day(tmp_path):
    """Two demon props on the same player on the same day should hit
    or miss together more often than chance, because the synthetic
    model conditions both on the player's good-day flag.

    Independence baseline under demon hit rate p_win = 0.4 gives
    agreement = 0.4^2 + 0.6^2 = 0.52. The good-day mixture lifts
    expected agreement to ~0.565 under the default config:

      P(both WIN) = 0.5*(0.55^2) + 0.5*(0.25^2) = 0.181
      P(both LOSS) = 0.5*(0.45^2) + 0.5*(0.75^2) = 0.383
      total       ~ 0.564

    Threshold sits comfortably above the 0.52 independence floor and
    inside the 0.564 expected mixture, leaving room for sampling
    variance.
    """
    from edge_equation.engines.tiering import Tier
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    rows = []
    n_players = 400
    for i in range(n_players):
        rows.append(_row(pid=f"a{i}", player=f"P{i}", stat="Hits", odds_type="demon"))
        rows.append(_row(pid=f"b{i}", player=f"P{i}", stat="Total Bases", odds_type="demon"))
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", rows)
    _, slates = load_props_backfill(
        tmp_path, config=PropSyntheticConfig(max_legs_per_date=10_000),
        min_tier=Tier.NO_PLAY,
    )
    by_player: dict[str, list[str]] = {}
    for g in slates[0].graded_legs:
        by_player.setdefault(g.leg.player_id, []).append(g.result)
    pairs = [v for v in by_player.values() if len(v) == 2]
    assert len(pairs) >= 200, f"only {len(pairs)} pairs survived; expected 200+"
    agree = sum(1 for v in pairs if v[0] == v[1])
    rate = agree / len(pairs)
    assert rate > 0.55, (
        f"agreement rate {rate:.3f} not above the 0.55 floor (independence "
        f"baseline is 0.52); same-player correlation isn't being injected "
        f"as designed."
    )


def test_seeded_runs_are_deterministic(tmp_path):
    from edge_equation.parlay_lab.backfill_props import (
        PropSyntheticConfig, load_props_backfill,
    )
    rows = [_row(pid=str(i), player=f"P{i}") for i in range(20)]
    _write_snapshot(tmp_path, "prizepicks_2026-05-01T18-00-00Z.csv", rows)
    cfg = PropSyntheticConfig(seed=123, max_legs_per_date=20)
    _, a = load_props_backfill(tmp_path, config=cfg)
    _, b = load_props_backfill(tmp_path, config=cfg)
    # Both runs should produce the same outcome stream.
    res_a = [(g.leg.side_probability, g.result) for g in a[0].graded_legs]
    res_b = [(g.leg.side_probability, g.result) for g in b[0].graded_legs]
    assert res_a == res_b
