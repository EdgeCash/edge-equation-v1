"""Tests for the player-id lookup resolver + cache."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from edge_equation.engines.props_prizepicks.data.player_id_lookup import (
    PlayerIdResolver,
    _normalize_name,
    _split_name,
)


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


def test_normalize_strips_accents():
    assert _normalize_name("Ramón Laureano") == "ramon laureano"
    assert _normalize_name("José Ramírez") == "jose ramirez"


def test_normalize_lowercases_and_collapses_whitespace():
    assert _normalize_name("  Aaron   Judge  ") == "aaron judge"


def test_normalize_empty_returns_empty():
    assert _normalize_name("") == ""
    assert _normalize_name(None) == ""  # type: ignore[arg-type]


def test_split_name_simple_two_word():
    assert _split_name("Aaron Judge") == ("judge", "aaron")


def test_split_name_handles_multi_word_last_name():
    assert _split_name("Elly De La Cruz") == ("de la cruz", "elly")


def test_split_name_single_token_treats_as_last_only():
    assert _split_name("Ohtani") == ("ohtani", "")


def test_split_name_empty_returns_empty_pair():
    assert _split_name("") == ("", "")


# ---------------------------------------------------------------------------
# Resolver — cache hit / miss / persistence
# ---------------------------------------------------------------------------


class _FakePyBaseball:
    """Stand-in for pybaseball with a controllable lookup table."""

    def __init__(self, table: dict[tuple[str, str], int | None]):
        self.table = table
        self.calls: list[tuple[str, str]] = []

    def playerid_lookup(self, last: str, first: str):
        import pandas as pd
        self.calls.append((last, first))
        result = self.table.get((last.lower(), first.lower()))
        if result is None:
            return pd.DataFrame()  # empty → caller treats as None
        return pd.DataFrame([{"key_mlbam": result, "mlb_played_last": 2025}])


def _install_fake_pybaseball(table) -> _FakePyBaseball:
    fake = _FakePyBaseball(table)
    mod = types.ModuleType("pybaseball")
    mod.playerid_lookup = fake.playerid_lookup  # type: ignore[attr-defined]
    sys.modules["pybaseball"] = mod
    return fake


def _uninstall_pybaseball():
    sys.modules.pop("pybaseball", None)


@pytest.fixture
def fake_pybaseball():
    fake = _install_fake_pybaseball({
        ("judge", "aaron"): 592450,
        ("ramirez", "jose"): 608070,
    })
    yield fake
    _uninstall_pybaseball()


def test_resolver_cache_miss_calls_pybaseball(tmp_path: Path, fake_pybaseball):
    resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
    assert resolver.resolve("Aaron Judge") == 592450
    assert fake_pybaseball.calls == [("judge", "aaron")]


def test_resolver_cache_hit_skips_pybaseball(tmp_path: Path, fake_pybaseball):
    resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
    resolver.resolve("Aaron Judge")
    fake_pybaseball.calls.clear()
    resolver.resolve("Aaron Judge")
    assert fake_pybaseball.calls == []


def test_resolver_negative_cache_prevents_repeat_lookups(
    tmp_path: Path, fake_pybaseball,
):
    resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
    assert resolver.resolve("Some Misspelled Name") is None
    fake_pybaseball.calls.clear()
    # Second lookup should hit the negative cache.
    assert resolver.resolve("Some Misspelled Name") is None
    assert fake_pybaseball.calls == []


def test_resolver_normalizes_accents_for_cache_key(
    tmp_path: Path, fake_pybaseball,
):
    """Ramón / Ramon should hash to the same cache slot."""
    fake_pybaseball.table[("laureano", "ramon")] = 666971
    resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
    assert resolver.resolve("Ramón Laureano") == 666971
    fake_pybaseball.calls.clear()
    assert resolver.resolve("Ramon Laureano") == 666971
    # Should be a cache hit, not a fresh call.
    assert fake_pybaseball.calls == []


def test_resolver_persists_cache_across_instances(
    tmp_path: Path, fake_pybaseball,
):
    cache_path = tmp_path / "ids.json"
    r1 = PlayerIdResolver(cache_path=cache_path)
    r1.resolve("Aaron Judge")
    r1.resolve("Definitely Not A Player")  # negative
    r1.save()

    fake_pybaseball.calls.clear()
    r2 = PlayerIdResolver(cache_path=cache_path)
    assert r2.resolve("Aaron Judge") == 592450
    assert r2.resolve("Definitely Not A Player") is None
    # Both should be cache hits — no new pybaseball calls.
    assert fake_pybaseball.calls == []


def test_resolver_save_writes_json_with_sorted_keys(
    tmp_path: Path, fake_pybaseball,
):
    cache_path = tmp_path / "ids.json"
    resolver = PlayerIdResolver(cache_path=cache_path)
    resolver.resolve("Aaron Judge")
    resolver.resolve("Jose Ramirez")
    resolver.save()
    payload = json.loads(cache_path.read_text())
    assert payload == {"aaron judge": 592450, "jose ramirez": 608070}


def test_resolver_save_idempotent_when_clean(
    tmp_path: Path, fake_pybaseball,
):
    cache_path = tmp_path / "ids.json"
    resolver = PlayerIdResolver(cache_path=cache_path)
    resolver.resolve("Aaron Judge")
    resolver.save()
    mtime1 = cache_path.stat().st_mtime
    # Second save with no new lookups should be a no-op (file unchanged).
    resolver.save()
    mtime2 = cache_path.stat().st_mtime
    assert mtime1 == mtime2


def test_resolver_swallows_pybaseball_errors(tmp_path: Path):
    """A pybaseball.playerid_lookup that raises should yield None,
    not propagate to the caller."""
    mod = types.ModuleType("pybaseball")

    def boom(last, first):
        raise RuntimeError("Chadwick API down")
    mod.playerid_lookup = boom  # type: ignore[attr-defined]
    sys.modules["pybaseball"] = mod
    try:
        resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
        assert resolver.resolve("Aaron Judge") is None
    finally:
        _uninstall_pybaseball()


def test_resolver_returns_none_when_pybaseball_missing(tmp_path: Path):
    """Without pybaseball installed, the resolver returns None for
    every name (gate at the daily orchestrator filters those picks)."""
    _uninstall_pybaseball()
    resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
    # Force ImportError path
    assert resolver.resolve("Aaron Judge") is None


def test_resolver_picks_most_recent_player_when_multiple_matches(
    tmp_path: Path,
):
    """When Chadwick returns multiple rows for a common name, pick
    the one with the most-recent last MLB season."""
    import pandas as pd

    mod = types.ModuleType("pybaseball")

    def lookup(last, first):
        return pd.DataFrame([
            {"key_mlbam": 100, "mlb_played_last": 1965},   # retired
            {"key_mlbam": 200, "mlb_played_last": 2025},   # active
            {"key_mlbam": 300, "mlb_played_last": 1980},
        ])

    mod.playerid_lookup = lookup  # type: ignore[attr-defined]
    sys.modules["pybaseball"] = mod
    try:
        resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
        assert resolver.resolve("John Smith") == 200
    finally:
        _uninstall_pybaseball()


def test_resolver_stats_counts_resolved_and_negative(
    tmp_path: Path, fake_pybaseball,
):
    resolver = PlayerIdResolver(cache_path=tmp_path / "ids.json")
    resolver.resolve("Aaron Judge")     # resolved
    resolver.resolve("Jose Ramirez")    # resolved
    resolver.resolve("Not Real Player") # negative
    s = resolver.stats()
    assert s["n_total_cached"] == 3
    assert s["n_resolved"] == 2
    assert s["n_negative"] == 1
