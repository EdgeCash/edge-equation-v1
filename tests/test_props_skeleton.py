"""Tests for the MLB player-props engine skeleton (Phase 4).

Pure-Python — the Odds API client is mocked via a fake httpx client so
the suite stays runnable without network access. All four modules are
covered: markets, odds_fetcher (fetch + normalize), projection (Poisson
math + per-side prob), edge (de-vig + tier classification).
"""

from __future__ import annotations

from typing import Any

import pytest

from edge_equation.engines.props_prizepicks import (
    MLB_PROP_MARKETS,
    PlayerPropLine,
    ProjectedSide,
    PropEdgePick,
    PropMarket,
    all_markets,
    build_devig_table,
    build_edge_picks,
    compute_edge_pp,
    fetch_event_list,
    fetch_event_player_props,
    market_for_odds_api_key,
    normalize_event_payload,
    project_all,
    project_player_market_prob,
)
from edge_equation.engines.props_prizepicks.projection import (
    _poisson_cdf,
    _prob_over_poisson,
)
from edge_equation.engines.tiering import Tier


# ---------------------------------------------------------------------------
# Markets — canonical mapping
# ---------------------------------------------------------------------------


def test_mlb_prop_markets_cover_core_set():
    """Skeleton must cover the five highest-volume MLB prop markets."""
    canonical = set(MLB_PROP_MARKETS.keys())
    assert canonical >= {"HR", "Hits", "Total_Bases", "RBI", "K"}


def test_mlb_prop_markets_have_unique_odds_api_keys():
    keys = [m.odds_api_key for m in MLB_PROP_MARKETS.values()]
    assert len(keys) == len(set(keys))


def test_market_for_odds_api_key_round_trip():
    for canonical, m in MLB_PROP_MARKETS.items():
        back = market_for_odds_api_key(m.odds_api_key)
        assert back is not None
        assert back.canonical == canonical


def test_market_for_odds_api_key_unknown_returns_none():
    assert market_for_odds_api_key("not_a_real_market") is None


def test_all_markets_returns_immutable_tuple():
    out = all_markets()
    assert isinstance(out, tuple)
    assert len(out) >= 5


# ---------------------------------------------------------------------------
# Poisson math sanity
# ---------------------------------------------------------------------------


def test_poisson_cdf_zero_lam_is_one():
    """Poisson(0) is degenerate at 0 — CDF at any k≥0 is 1."""
    assert _poisson_cdf(0, 0.0) == 1.0
    assert _poisson_cdf(5, 0.0) == 1.0


def test_poisson_cdf_negative_k_is_zero():
    assert _poisson_cdf(-1, 1.0) == 0.0


def test_prob_over_half_at_lam_one_is_under_half():
    """Poisson(1) puts ~37% mass at 0; Over 0.5 should be ~63%."""
    p = _prob_over_poisson(0.5, 1.0)
    assert 0.60 < p < 0.66


def test_prob_over_one_half_matches_at_high_lam():
    """High lambda → P(over high line) ≈ 0.5 around the mean."""
    p = _prob_over_poisson(5.5, 6.0)
    assert 0.40 < p < 0.65


# ---------------------------------------------------------------------------
# Projection — single line + bulk
# ---------------------------------------------------------------------------


def _line(canonical="HR", side="Over", line_value=0.5,
           american_odds=+250, player="Test Player",
           event_id="evt1"):
    m = MLB_PROP_MARKETS[canonical]
    return PlayerPropLine(
        event_id=event_id, home_team="BOS", away_team="NYY",
        commence_time="2026-04-29T23:05:00Z", market=m,
        player_name=player, side=side, line_value=line_value,
        american_odds=float(american_odds),
        decimal_odds=2.5 if american_odds > 0 else 1.5,
        book="draftkings",
    )


def test_projection_under_side_complements_over():
    """Over + Under on the same line must sum to 1.0 (within float ε)."""
    over = project_player_market_prob(_line(side="Over"))
    under = project_player_market_prob(_line(side="Under"))
    assert over.model_prob + under.model_prob == pytest.approx(1.0, abs=1e-9)


def test_projection_uses_rate_override_when_provided():
    base = project_player_market_prob(_line(side="Over"))
    boosted = project_player_market_prob(_line(side="Over"),
                                            rate_override=1.0)
    # Higher rate → higher P(over 0.5)
    assert boosted.model_prob > base.model_prob


def test_projection_yes_alias_treated_as_over():
    """Some books label HR as 'Yes/No' — treat 'Yes' as 'Over'."""
    yes = project_player_market_prob(_line(side="Yes"))
    over = project_player_market_prob(_line(side="Over"))
    assert yes.model_prob == pytest.approx(over.model_prob, abs=1e-9)


def test_project_all_preserves_input_order():
    lines = [_line(player=f"p{i}") for i in range(5)]
    out = project_all(lines)
    assert [p.player_name for p in out] == ["p0", "p1", "p2", "p3", "p4"]


def test_project_all_applies_rate_overrides_per_player():
    lines = [
        _line(player="Judge"),
        _line(player="Trout"),
    ]
    out = project_all(lines, rate_overrides={
        ("Judge", "HR"): 0.6,
        "HR": 0.05,           # market-wide fallback for everyone else
    })
    judge = next(p for p in out if p.player_name == "Judge")
    trout = next(p for p in out if p.player_name == "Trout")
    # Judge's per-player override beats the market-wide fallback.
    assert judge.model_prob > trout.model_prob


# ---------------------------------------------------------------------------
# De-vig table
# ---------------------------------------------------------------------------


def test_devig_table_pairs_over_under_on_same_line():
    over = _line(side="Over",  american_odds=+150, player="A")
    under = _line(side="Under", american_odds=-180, player="A")
    table = build_devig_table([over, under])
    assert len(table) == 1
    total = list(table.values())[0]
    # Implied probs sum to >1 (vig). With +150/-180 the total is ~1.04.
    assert 1.02 < total < 1.10


def test_devig_table_skips_unpaired_sides():
    over = _line(side="Over", player="A")
    table = build_devig_table([over])
    assert table == {}


def test_devig_table_keys_distinguish_players():
    a_over = _line(side="Over",  player="A", american_odds=+150)
    a_under = _line(side="Under", player="A", american_odds=-180)
    b_over = _line(side="Over",  player="B", american_odds=+200)
    table = build_devig_table([a_over, a_under, b_over])
    # B has no pair → not in the table; A has a pair → in the table.
    assert len(table) == 1


# ---------------------------------------------------------------------------
# Edge math
# ---------------------------------------------------------------------------


def test_compute_edge_pp_positive_when_model_beats_book():
    line = _line(side="Over", american_odds=+250)  # implied ~28.6%
    proj = ProjectedSide(market=line.market, player_name=line.player_name,
                          line_value=0.5, side="Over",
                          model_prob=0.40, confidence=0.5)
    edge_pp, raw, devigged, corrected = compute_edge_pp(
        line=line, projection=proj,
    )
    assert edge_pp > 0
    assert raw == pytest.approx(0.286, abs=0.01)
    assert corrected is False  # no devig pair


def test_compute_edge_pp_negative_when_model_under_book():
    line = _line(side="Over", american_odds=-200)  # implied 66.7%
    proj = ProjectedSide(market=line.market, player_name=line.player_name,
                          line_value=0.5, side="Over",
                          model_prob=0.50, confidence=0.5)
    edge_pp, *_ = compute_edge_pp(line=line, projection=proj)
    assert edge_pp < 0


def test_compute_edge_pp_uses_devig_total_when_provided():
    line = _line(side="Over", american_odds=+150)  # raw implied 0.40
    proj = ProjectedSide(market=line.market, player_name=line.player_name,
                          line_value=0.5, side="Over",
                          model_prob=0.50, confidence=0.5)
    # Pair sums to 1.04 (typical vig) → de-vigged ≈ 0.40/1.04 = 0.385
    edge_pp, raw, devigged, corrected = compute_edge_pp(
        line=line, projection=proj, devig_total=1.04,
    )
    assert corrected is True
    assert devigged == pytest.approx(raw / 1.04, abs=1e-6)
    # De-vigging shrinks the book's price → larger model edge.
    assert edge_pp > (proj.model_prob - raw) * 100.0


# ---------------------------------------------------------------------------
# build_edge_picks — end-to-end
# ---------------------------------------------------------------------------


def test_build_edge_picks_filters_below_min_tier():
    """A 0.5pp edge classifies as NO_PLAY on the edge ladder; with
    `min_tier=Tier.LEAN` it must not be returned."""
    line = _line(side="Over", american_odds=-110)
    proj = ProjectedSide(market=line.market, player_name=line.player_name,
                          line_value=0.5, side="Over",
                          # implied is ~52.4%; model 53.0% → 0.6pp edge → NO_PLAY
                          model_prob=0.53, confidence=0.5)
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert picks == []


def test_build_edge_picks_classifies_strong_at_5pp():
    line = _line(side="Over", american_odds=+250)  # implied ~28.6%
    proj = ProjectedSide(market=line.market, player_name=line.player_name,
                          line_value=0.5, side="Over",
                          model_prob=0.36, confidence=0.5)  # 7.4pp edge
    picks = build_edge_picks([line], [proj], min_tier=Tier.LEAN)
    assert len(picks) == 1
    assert picks[0].tier == Tier.STRONG
    assert picks[0].edge_pp > 5.0


def test_build_edge_picks_sorts_by_edge_desc():
    a = _line(side="Over", player="big_edge",   american_odds=+300)
    a_proj = ProjectedSide(market=a.market, player_name=a.player_name,
                              line_value=0.5, side="Over",
                              model_prob=0.40, confidence=0.5)
    b = _line(side="Over", player="small_edge", american_odds=+250)
    b_proj = ProjectedSide(market=b.market, player_name=b.player_name,
                              line_value=0.5, side="Over",
                              model_prob=0.32, confidence=0.5)
    picks = build_edge_picks([b, a], [b_proj, a_proj], min_tier=Tier.LEAN)
    assert [p.player_name for p in picks] == ["big_edge", "small_edge"]


def test_build_edge_picks_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        build_edge_picks([_line()], [], min_tier=Tier.LEAN)


# ---------------------------------------------------------------------------
# Odds API normalization
# ---------------------------------------------------------------------------


def test_normalize_event_payload_extracts_player_lines():
    payload = {
        "id": "evt1", "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "commence_time": "2026-04-29T23:05:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "batter_home_runs", "outcomes": [
                    {"name": "Over",  "description": "Aaron Judge",
                       "point": 0.5, "price": +250},
                    {"name": "Under", "description": "Aaron Judge",
                       "point": 0.5, "price": -350},
                ]},
                {"key": "pitcher_strikeouts", "outcomes": [
                    {"name": "Over",  "description": "Garrett Crochet",
                       "point": 7.5, "price": -115},
                    {"name": "Under", "description": "Garrett Crochet",
                       "point": 7.5, "price": -105},
                ]},
            ]},
        ],
    }
    rows = normalize_event_payload(payload)
    by_key = {(r.player_name, r.market.canonical, r.side): r for r in rows}
    assert ("Aaron Judge", "HR", "Over") in by_key
    assert ("Aaron Judge", "HR", "Under") in by_key
    assert ("Garrett Crochet", "K", "Over") in by_key
    judge_over = by_key[("Aaron Judge", "HR", "Over")]
    assert judge_over.line_value == 0.5
    assert judge_over.american_odds == +250
    assert judge_over.book == "draftkings"


def test_normalize_event_payload_skips_unmapped_markets():
    payload = {
        "id": "evt1", "home_team": "BOS", "away_team": "NYY",
        "commence_time": "2026-04-29T23:05:00Z",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Boston Red Sox", "price": -120},
                ]},
            ]},
        ],
    }
    rows = normalize_event_payload(payload)
    assert rows == []


def test_normalize_event_payload_handles_empty_bookmakers():
    rows = normalize_event_payload({"bookmakers": []})
    assert rows == []


# ---------------------------------------------------------------------------
# Fake-httpx fetch tests
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status
    def json(self): return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)
    def close(self): pass


def test_fetch_event_list_threads_apikey_param():
    client = _FakeClient([_FakeResponse([
        {"id": "evt1", "home_team": "BOS", "away_team": "NYY",
           "commence_time": "2026-04-29T23:05:00Z"},
    ])])
    out = fetch_event_list(http_client=client, api_key="secret")
    assert len(out) == 1
    _, params = client.calls[0]
    assert params["apiKey"] == "secret"
    assert params["regions"] == "us"


def test_fetch_event_player_props_passes_markets_string():
    client = _FakeClient([_FakeResponse({"bookmakers": []})])
    fetch_event_player_props(
        event_id="evt1", api_key="secret", http_client=client,
        markets_param="batter_home_runs,pitcher_strikeouts",
    )
    _, params = client.calls[0]
    assert params["markets"] == "batter_home_runs,pitcher_strikeouts"


def test_fetch_event_list_raises_on_unexpected_shape():
    client = _FakeClient([_FakeResponse({"error": "rate-limited"})])
    with pytest.raises(ValueError):
        fetch_event_list(http_client=client, api_key="secret")
