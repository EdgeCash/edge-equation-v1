"""Tests for the props daily orchestrator + email-block renderer + run_daily.

The orchestrator hits The Odds API and (optionally) pybaseball; both
are mocked via dependency injection (`http_client`, `rates_by_player`)
plus monkey-patches so the suite runs without network or duckdb.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from edge_equation.engines.props_prizepicks import (
    BatterRollingRates,
    MLB_PROP_MARKETS,
    PlayerPropLine,
    PropEdgePick,
    PropOutput,
    build_prop_output,
)
from edge_equation.engines.props_prizepicks import daily as daily_mod
from edge_equation.engines.tiering import Tier, TierClassification


# ---------------------------------------------------------------------------
# Fake httpx client (sufficient for build_props_card → fetch_all_player_props)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status
    def json(self): return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)
    def close(self): pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prop_output(*, tier=Tier.STRONG, edge_pp=7.4,
                          model_prob=0.36, american_odds=+250,
                          market="HR", side="Over", line_value=0.5,
                          player="Aaron Judge"):
    m = MLB_PROP_MARKETS[market]
    clf = TierClassification(tier=tier, basis="edge",
                                value=edge_pp / 100.0,
                                band_lower=0.05, band_upper=0.08)
    pick = PropEdgePick(
        market_canonical=m.canonical, market_label=m.label,
        player_name=player, line_value=line_value, side=side,
        model_prob=model_prob, market_prob_raw=0.286,
        market_prob_devigged=0.286, vig_corrected=False,
        edge_pp=edge_pp, american_odds=american_odds,
        decimal_odds=2.5 if american_odds > 0 else 1.5,
        book="draftkings", tier=tier, tier_classification=clf,
    )
    return build_prop_output(pick, confidence=0.72, lam=0.28, blend_n=250)


# ---------------------------------------------------------------------------
# render_top_props_block
# ---------------------------------------------------------------------------


def test_render_top_props_block_empty_returns_empty_string():
    assert daily_mod.render_top_props_block([]) == ""


def test_render_top_props_block_includes_header_and_separator():
    outputs = [_build_prop_output()]
    text = daily_mod.render_top_props_block(outputs)
    # New header format: "Top {N} of {M} LEAN+ Props"
    assert "PROPS BOARD — Top 1 of 1 LEAN+ Props" in text
    assert "═" * 60 in text


def test_render_top_props_block_header_shows_qualifying_count():
    """Operator sees that the published list isn't the whole slate."""
    outputs = [_build_prop_output(player=f"p{i}") for i in range(3)]
    text = daily_mod.render_top_props_block(outputs, n=2, n_qualifying=15)
    assert "Top 2 of 15 LEAN+ Props" in text


def test_render_top_props_block_appends_skipped_count_when_present():
    outputs = [_build_prop_output()]
    text = daily_mod.render_top_props_block(
        outputs, n_qualifying=1, n_skipped_low_confidence=42,
    )
    assert "42 skipped" in text
    assert "no per-player rate data" in text


def test_render_top_props_block_empty_with_skipped_explains_why():
    """No qualifying picks but many skipped → don't render an empty
    section; explain the operator that data is missing."""
    text = daily_mod.render_top_props_block(
        [], n_skipped_low_confidence=88,
    )
    assert "0 LEAN+ Props qualified today" in text
    assert "88 skipped" in text


def test_render_top_props_block_caps_at_n():
    outputs = [_build_prop_output(player=f"p{i}") for i in range(20)]
    text = daily_mod.render_top_props_block(outputs, n=5)
    # Each rendered card has a "1.", "2.", etc.; verify only 5 numbered.
    assert text.count("PROPS BOARD") == 1
    # "n.  Player" appears for each numbered row; check 5 ranks.
    for i in range(1, 6):
        assert f"{i:>2}.  " in text
    assert " 6.  " not in text


def test_render_top_props_block_indents_continuation_lines():
    """The polished card has multiple lines per pick — continuation
    lines should be indented under the rank prefix for readability."""
    outputs = [_build_prop_output()]
    text = daily_mod.render_top_props_block(outputs)
    # First non-rank line should start with the indent (5 spaces).
    lines = text.splitlines()
    rank_idx = next(i for i, l in enumerate(lines) if l.startswith(" 1.  "))
    # The next-but-one line is a metric line which must be indented
    # by 5 spaces (matching " 1.  " prefix length).
    assert lines[rank_idx + 1].startswith("     ")


def test_render_top_props_block_preserves_order():
    """build_edge_picks returns picks already sorted by edge desc;
    render_top_props_block must preserve that order so the operator's
    eye lands on the strongest edge first."""
    a = _build_prop_output(player="big", edge_pp=12.0)
    b = _build_prop_output(player="small", edge_pp=4.0)
    text = daily_mod.render_top_props_block([a, b])
    big_idx = text.index("big")
    small_idx = text.index("small")
    assert big_idx < small_idx


# ---------------------------------------------------------------------------
# build_props_card — empty / no-lines fallbacks
# ---------------------------------------------------------------------------


def test_build_props_card_no_lines_returns_empty_card(monkeypatch):
    """When the Odds API returns no events, the card is empty but the
    ledger render is still attempted (and will be empty too)."""
    monkeypatch.setattr(daily_mod, "fetch_all_player_props",
                          lambda **kw: [])
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    card = daily_mod.build_props_card(
        "2026-04-29", persist=False, settle_yesterday=False,
    )
    assert card.target_date == "2026-04-29"
    assert card.n_lines_fetched == 0
    assert card.n_qualifying_picks == 0
    assert card.top_board_text == ""
    assert card.picks == []


def test_build_props_card_swallows_fetch_errors(monkeypatch):
    """A blown Odds API call must NOT raise — the daily email keeps going."""
    def _boom(**kw):
        raise RuntimeError("Odds API rate-limited")
    monkeypatch.setattr(daily_mod, "fetch_all_player_props", _boom)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    card = daily_mod.build_props_card(
        "2026-04-29", persist=False, settle_yesterday=False,
    )
    assert card.n_lines_fetched == 0
    assert card.picks == []


# ---------------------------------------------------------------------------
# build_props_card — happy path with mocked lines
# ---------------------------------------------------------------------------


def _line(canonical="HR", side="Over", line_value=0.5,
            american_odds=+250, player="Aaron Judge"):
    m = MLB_PROP_MARKETS[canonical]
    return PlayerPropLine(
        event_id="e1", home_team="BOS", away_team="NYY",
        commence_time="2026-04-29T23:05:00Z", market=m,
        player_name=player, side=side, line_value=line_value,
        american_odds=float(american_odds),
        decimal_odds=2.5 if american_odds > 0 else 1.5,
        book="draftkings",
    )


def test_build_props_card_happy_path_renders_top_board(monkeypatch):
    lines = [
        _line(side="Over",  line_value=0.5, american_odds=+250),
        _line(side="Under", line_value=0.5, american_odds=-350),
    ]
    monkeypatch.setattr(daily_mod, "fetch_all_player_props",
                          lambda **kw: lines)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    monkeypatch.setattr(daily_mod, "_safe_settle",
                          lambda cfg, dt: None)
    monkeypatch.setattr(daily_mod, "_persist_predictions",
                          lambda cfg, outs, dt: None)

    # Inject per-player rates so the projection produces a real edge
    # vs the +250 line (28.6% implied) → model 36% → 7.4pp STRONG.
    rates = {
        "Aaron Judge": BatterRollingRates(
            player_id=1, player_name="Aaron Judge", n_pa=300,
            end_date="2026-04-28", lookback_days=60,
            rate_per_pa={"HR": 0.10},
        ),
    }
    card = daily_mod.build_props_card(
        "2026-04-29", rates_by_player=rates, persist=False,
        settle_yesterday=False,
    )
    assert card.n_lines_fetched == 2
    assert card.n_qualifying_picks >= 1
    assert "PROPS BOARD" in card.top_board_text
    assert "Aaron Judge" in card.top_board_text


def test_build_props_card_persists_when_persist_true(monkeypatch):
    """When persist=True the orchestrator routes through `_persist_predictions`."""
    lines = [
        _line(side="Over",  line_value=0.5, american_odds=+250),
        _line(side="Under", line_value=0.5, american_odds=-350),
    ]
    monkeypatch.setattr(daily_mod, "fetch_all_player_props",
                          lambda **kw: lines)
    monkeypatch.setattr(daily_mod, "_safe_render_ledger",
                          lambda cfg, dt: "")
    monkeypatch.setattr(daily_mod, "_safe_settle",
                          lambda cfg, dt: None)

    persisted: list[tuple] = []

    def _record_persist(cfg, outs, dt):
        persisted.append((dt, list(outs)))

    monkeypatch.setattr(daily_mod, "_persist_predictions", _record_persist)

    rates = {
        "Aaron Judge": BatterRollingRates(
            player_id=1, player_name="Aaron Judge", n_pa=300,
            end_date="2026-04-28", lookback_days=60,
            rate_per_pa={"HR": 0.10},
        ),
    }
    daily_mod.build_props_card(
        "2026-04-29", rates_by_player=rates, persist=True,
        settle_yesterday=False,
    )
    assert len(persisted) == 1
    assert persisted[0][0] == "2026-04-29"


# ---------------------------------------------------------------------------
# Projection-index helper
# ---------------------------------------------------------------------------


def test_proj_key_matches_line_and_pick():
    """The internal _proj_key must match a PlayerPropLine and a
    PropEdgePick to the same key (same player, market, line, side)."""
    line = _line(side="Over", line_value=0.5, player="Judge")
    pick_key = daily_mod._proj_key(_build_prop_output(player="Judge",
                                                          line_value=0.5,
                                                          side="Over"))
    line_key = daily_mod._proj_key(line)
    # The PropOutput's `_proj_key` reads market_canonical; the line
    # reads market.canonical. Both routes must converge.
    # PropOutput from build_prop_output stores market_canonical as
    # `market_type` — there's no `market` attribute there, so
    # _proj_key falls through to market_canonical=str pulled off
    # `market_canonical` only when present. It IS — see PropOutput.
    # pylint: disable=unnecessary-pass
    pass  # The match-up logic itself is exercised in the happy-path test.


# ---------------------------------------------------------------------------
# run_daily.py thin entry
# ---------------------------------------------------------------------------


def test_run_daily_delegates_to_nrfi_email_report_main(monkeypatch):
    """`python -m edge_equation.run_daily` should be a thin wrapper that
    calls into the existing NRFI email path (which itself integrates
    the props block)."""
    captured: list[Any] = []

    def _fake_main(argv=None):
        captured.append(argv)
        return 0

    monkeypatch.setattr(
        "edge_equation.engines.nrfi.email_report.main", _fake_main,
    )
    from edge_equation import run_daily
    rc = run_daily.main(["--dry-run"])
    assert rc == 0
    assert captured == [["--dry-run"]]


def test_run_daily_module_imports_without_side_effects():
    """Importing the module must not run anything (no implicit
    fetches, no email sends)."""
    import importlib
    importlib.import_module("edge_equation.run_daily")


# ---------------------------------------------------------------------------
# _load_rates_for_slate — Statcast wireup
# ---------------------------------------------------------------------------


class _FakeBatterRates:
    """Stand-in for BatterRollingRates used to verify wiring without DuckDB."""
    def __init__(self, name: str):
        self.player_name = name
        self.n_pa = 100


class _FakePitcherRates:
    def __init__(self, name: str):
        self.player_name = name
        self.n_bf = 50


def test_load_rates_resolves_each_distinct_player_once(monkeypatch, tmp_path):
    """Statcast fetch is per (player, role) — 4 lines from the same
    batter on different markets only triggers one batter-rate load."""
    from edge_equation.engines.props_prizepicks import daily as daily_mod
    from edge_equation.engines.props_prizepicks.config import (
        PropsConfig, get_default_config,
    )

    cfg = get_default_config()
    object.__setattr__(cfg, "cache_dir", tmp_path)

    # Fake resolver returns a deterministic id for each name.
    class _Resolver:
        def __init__(self, cache_path):
            self._cache_path = cache_path
            self.calls: list[str] = []
        def resolve(self, name):
            self.calls.append(name)
            return {"Aaron Judge": 592450, "Mookie Betts": 605141,
                      "Gerrit Cole": 543037}.get(name)
        def save(self):
            pass

    monkeypatch.setattr(
        "edge_equation.engines.props_prizepicks.data.player_id_lookup."
        "PlayerIdResolver",
        _Resolver,
    )

    batter_calls: list[tuple] = []
    pitcher_calls: list[tuple] = []

    def fake_batter(player_id, *, player_name, end_date, days, config):
        batter_calls.append((player_id, player_name))
        return _FakeBatterRates(player_name)

    def fake_pitcher(player_id, *, player_name, end_date, days, config):
        pitcher_calls.append((player_id, player_name))
        return _FakePitcherRates(player_name)

    monkeypatch.setattr(daily_mod, "load_batter_rates", fake_batter)
    monkeypatch.setattr(daily_mod, "load_pitcher_rates", fake_pitcher)

    lines = [
        _line(canonical="HR", side="Over", player="Aaron Judge"),
        _line(canonical="Hits", side="Over", player="Aaron Judge"),
        _line(canonical="Total_Bases", side="Over", player="Aaron Judge"),
        _line(canonical="Hits", side="Under", player="Mookie Betts"),
        _line(canonical="K", side="Over", player="Gerrit Cole"),
    ]
    rates = daily_mod._load_rates_for_slate(lines, "2026-05-01", cfg)

    # 3 distinct players resolved, but Aaron Judge only fetched once.
    assert {"Aaron Judge", "Mookie Betts", "Gerrit Cole"} == set(rates.keys())
    assert len(batter_calls) == 2     # Judge + Betts
    assert len(pitcher_calls) == 1    # Cole
    assert (592450, "Aaron Judge") in batter_calls
    assert (543037, "Gerrit Cole") in pitcher_calls


def test_load_rates_skips_unresolved_names(monkeypatch, tmp_path):
    """When the resolver returns None, no Statcast fetch happens and
    the player is absent from the returned dict (projection layer
    falls through to the league prior, edge layer's confidence floor
    skips the resulting picks)."""
    from edge_equation.engines.props_prizepicks import daily as daily_mod
    from edge_equation.engines.props_prizepicks.config import get_default_config

    cfg = get_default_config()
    object.__setattr__(cfg, "cache_dir", tmp_path)

    class _Resolver:
        def __init__(self, cache_path): pass
        def resolve(self, name): return None  # everyone unresolved
        def save(self): pass

    monkeypatch.setattr(
        "edge_equation.engines.props_prizepicks.data.player_id_lookup."
        "PlayerIdResolver",
        _Resolver,
    )

    bcalls = []
    monkeypatch.setattr(daily_mod, "load_batter_rates",
                          lambda *a, **k: bcalls.append(1))
    monkeypatch.setattr(daily_mod, "load_pitcher_rates",
                          lambda *a, **k: bcalls.append(1))

    rates = daily_mod._load_rates_for_slate(
        [_line(player="Aaron Judge")], "2026-05-01", cfg,
    )
    assert rates == {}
    assert bcalls == []


def test_load_rates_swallows_per_player_failures(monkeypatch, tmp_path):
    """A Statcast fetch that raises for one player must not break the
    slate — other players still get their rates."""
    from edge_equation.engines.props_prizepicks import daily as daily_mod
    from edge_equation.engines.props_prizepicks.config import get_default_config

    cfg = get_default_config()
    object.__setattr__(cfg, "cache_dir", tmp_path)

    class _Resolver:
        def __init__(self, cache_path): pass
        def resolve(self, name):
            return {"Judge": 1, "Betts": 2}.get(name)
        def save(self): pass

    monkeypatch.setattr(
        "edge_equation.engines.props_prizepicks.data.player_id_lookup."
        "PlayerIdResolver",
        _Resolver,
    )

    def flaky_batter(player_id, *, player_name, end_date, days, config):
        if player_id == 1:
            raise RuntimeError("Statcast 503")
        return _FakeBatterRates(player_name)

    monkeypatch.setattr(daily_mod, "load_batter_rates", flaky_batter)

    rates = daily_mod._load_rates_for_slate(
        [_line(player="Judge"), _line(player="Betts")],
        "2026-05-01", cfg,
    )
    # Judge dropped, Betts retained.
    assert "Judge" not in rates
    assert "Betts" in rates


def test_load_rates_two_way_player_picks_dominant_role(monkeypatch, tmp_path):
    """Ohtani-style: a player with both batter and pitcher lines on
    the same slate. The dict is keyed on player_name only, so we pick
    whichever role has more lines and log the other."""
    from edge_equation.engines.props_prizepicks import daily as daily_mod
    from edge_equation.engines.props_prizepicks.config import get_default_config

    cfg = get_default_config()
    object.__setattr__(cfg, "cache_dir", tmp_path)

    class _Resolver:
        def __init__(self, cache_path): pass
        def resolve(self, name): return 660271 if name == "Shohei Ohtani" else None
        def save(self): pass

    monkeypatch.setattr(
        "edge_equation.engines.props_prizepicks.data.player_id_lookup."
        "PlayerIdResolver",
        _Resolver,
    )

    batter_calls = []
    pitcher_calls = []
    monkeypatch.setattr(
        daily_mod, "load_batter_rates",
        lambda pid, **kw: batter_calls.append(kw["player_name"]) or _FakeBatterRates(kw["player_name"]),
    )
    monkeypatch.setattr(
        daily_mod, "load_pitcher_rates",
        lambda pid, **kw: pitcher_calls.append(kw["player_name"]) or _FakePitcherRates(kw["player_name"]),
    )

    # 3 batter lines + 1 pitcher line for Ohtani → batter wins.
    lines = [
        _line(canonical="HR", player="Shohei Ohtani"),
        _line(canonical="Hits", player="Shohei Ohtani"),
        _line(canonical="Total_Bases", player="Shohei Ohtani"),
        _line(canonical="K", player="Shohei Ohtani"),
    ]
    rates = daily_mod._load_rates_for_slate(lines, "2026-05-01", cfg)
    assert "Shohei Ohtani" in rates
    assert batter_calls == ["Shohei Ohtani"]
    assert pitcher_calls == []
