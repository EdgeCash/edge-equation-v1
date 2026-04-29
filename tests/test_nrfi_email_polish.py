"""Tests for the NRFI-elite email polish (top board + humanized drivers).

The new render layout puts the YTD ledger at the top, then a TOP BOARD
section with the top 8 picks by edge in a polished one-liner format,
then the per-side NRFI / YRFI boards beneath.
"""

from __future__ import annotations

from edge_equation.engines.nrfi import email_report as er


# ---------------------------------------------------------------------------
# Driver humanization
# ---------------------------------------------------------------------------


def test_humanize_driver_translates_known_features():
    assert "home pitcher xERA" in er._humanize_driver("+4.2 home_p_xera")
    assert "umpire zone size" in er._humanize_driver("−2.1 ump_zone_idx")
    assert "park run factor" in er._humanize_driver("+0.8 park_factor_runs")


def test_humanize_driver_keeps_signed_delta_intact():
    out = er._humanize_driver("+4.2 home_xwoba_t90")
    assert out.startswith("+4.2 ")


def test_humanize_driver_passes_unknown_features_through():
    """An unmapped feature must not be hidden — operator can still
    see what the model is reacting to even if the label isn't pretty."""
    raw = "+1.5 some_unknown_feature_v7"
    assert er._humanize_driver(raw).endswith("some_unknown_feature_v7")


def test_humanize_driver_handles_malformed_input():
    """A weirdly-formatted driver string passes through unchanged
    rather than crashing."""
    assert er._humanize_driver("nodelta") == "nodelta"


# ---------------------------------------------------------------------------
# Top-N selection
# ---------------------------------------------------------------------------


def _pick(*, game_id, market="NRFI", tier="STRONG", pct=70.0,
            edge_pp_raw=None):
    return {
        "game_id": game_id, "market_type": market,
        "selection": market, "pct": pct, "fair_prob": str(pct / 100.0),
        "color_band": "Light Green", "color_hex": "#7cb342",
        "signal": "STRONG_NRFI", "lambda_total": 1.10,
        "mc_band_pp": 3.0, "edge": None, "edge_pp_raw": edge_pp_raw,
        "kelly": None, "kelly_units_raw": None, "grade": "A",
        "tier": tier, "tier_basis": "raw_probability",
        "drivers": [], "rendered": "fallback",
    }


def test_select_top_picks_keeps_one_row_per_game():
    """Both NRFI and YRFI rows of the same game must collapse to a
    single row (the higher-tier side)."""
    picks = [
        _pick(game_id="g1", market="NRFI", tier="ELITE",   pct=80.0),
        _pick(game_id="g1", market="YRFI", tier="NO_PLAY", pct=20.0),
        _pick(game_id="g2", market="NRFI", tier="STRONG", pct=68.0),
        _pick(game_id="g2", market="YRFI", tier="NO_PLAY", pct=32.0),
    ]
    top = er._select_top_picks_by_edge(picks, n=8)
    assert len(top) == 2
    by_gid = {p["game_id"]: p for p in top}
    # Higher-tier side wins per game.
    assert by_gid["g1"]["tier"] == "ELITE"
    assert by_gid["g2"]["tier"] == "STRONG"


def test_select_top_picks_sorts_by_tier_then_edge_then_pct():
    """LOCK ahead of STRONG; within STRONG, higher edge first; ties
    broken by NRFI%."""
    picks = [
        _pick(game_id="g1", tier="STRONG", pct=68.0, edge_pp_raw=2.0),
        _pick(game_id="g2", tier="STRONG", pct=68.0, edge_pp_raw=5.5),
        _pick(game_id="g3", tier="ELITE",   pct=72.0, edge_pp_raw=1.0),
    ]
    top = er._select_top_picks_by_edge(picks, n=8)
    assert [p["game_id"] for p in top] == ["g3", "g2", "g1"]


def test_select_top_picks_caps_at_n():
    picks = [_pick(game_id=f"g{i}", pct=80 - i) for i in range(20)]
    assert len(er._select_top_picks_by_edge(picks, n=8)) == 8


def test_select_top_picks_handles_missing_edge():
    """When all picks have edge_pp_raw=None, sort falls back cleanly
    on tier rank + nrfi_pct."""
    picks = [
        _pick(game_id="g1", tier="STRONG", pct=68.0, edge_pp_raw=None),
        _pick(game_id="g2", tier="STRONG", pct=72.0, edge_pp_raw=None),
        _pick(game_id="g3", tier="ELITE",   pct=70.0, edge_pp_raw=None),
    ]
    top = er._select_top_picks_by_edge(picks, n=8)
    assert top[0]["game_id"] == "g3"   # LOCK beats both STRONGs
    # Within STRONG, higher pct wins.
    assert top[1]["game_id"] == "g2"
    assert top[2]["game_id"] == "g1"


def test_select_top_picks_empty_returns_empty():
    assert er._select_top_picks_by_edge([], n=8) == []


# ---------------------------------------------------------------------------
# Polished pick line
# ---------------------------------------------------------------------------


def test_polished_pick_line_includes_tier_pct_color_and_lambda():
    pick = _pick(game_id="BOS @ NYY", tier="ELITE", pct=78.4)
    pick["lambda_total"] = 1.20
    line = er._polished_pick_line(pick)
    assert "BOS @ NYY" in line
    assert "[ELITE" in line
    # Branding rebrand: row reads "% Conviction" rather than "% NRFI".
    assert "78.4% Conviction" in line
    # ELITE tier renders as Electric Blue under the new color system.
    assert "Electric Blue" in line
    assert "λ 1.20" in line


def test_polished_pick_line_appends_mc_edge_kelly_when_present():
    pick = _pick(game_id="g1", tier="STRONG", pct=68.9, edge_pp_raw=5.1)
    pick["mc_band_pp"] = 3.2
    pick["edge"] = "+5.1pp"
    pick["kelly"] = "0.50u"
    line = er._polished_pick_line(pick)
    assert "MC ±3.2pp" in line
    assert "edge +5.1pp" in line
    assert "stake 0.50u" in line


def test_polished_pick_line_drops_optional_metrics_cleanly():
    pick = _pick(game_id="g1", tier="STRONG", pct=68.0)
    pick["mc_band_pp"] = None
    pick["edge"] = None
    pick["kelly"] = None
    line = er._polished_pick_line(pick)
    assert "MC ±" not in line
    assert "edge" not in line
    assert "stake" not in line


def test_polished_pick_line_humanizes_drivers_in_why_clause():
    pick = _pick(game_id="g1", tier="STRONG", pct=70.0)
    pick["drivers"] = [
        "+4.2 home_xwoba_t90",
        "−2.1 ump_zone_idx",
        "+0.8 park_factor_runs",
    ]
    line = er._polished_pick_line(pick)
    assert "Why:" in line
    assert "home offense xwOBA (90d)" in line
    assert "umpire zone size" in line
    assert "park run factor" in line


def test_polished_pick_line_caps_drivers_at_four():
    """Operator's eye lands on the top drivers — 5+ becomes a wall of
    text. Pin the cap at 4 so the line stays readable."""
    pick = _pick(game_id="g1", tier="STRONG", pct=70.0)
    pick["drivers"] = [f"+{i}.0 driver_{i}" for i in range(7)]
    line = er._polished_pick_line(pick)
    assert "driver_3" in line
    assert "driver_5" not in line   # 5th and 6th must be omitted


# ---------------------------------------------------------------------------
# render_body integration: ordering + always-on YTD ledger
# ---------------------------------------------------------------------------


def _minimal_card(**overrides):
    base = {
        "headline": "NRFI Daily — 2026-04-29",
        "subhead": "Facts. Not Feelings.",
        "tagline": "(footer)",
        "engine": "ml",
        "target_date": "2026-04-29",
        "picks": [],
        "ledger_text": "",
        "parlay_text": "",
        "parlay_ledger_text": "",
    }
    base.update(overrides)
    return base


def test_render_body_renders_ytd_placeholder_when_ledger_empty():
    body = er.render_body(_minimal_card())
    assert "YTD LEDGER (2026)" in body
    assert "no settled picks yet" in body


def test_render_body_renders_real_ytd_ledger_when_present():
    body = er.render_body(_minimal_card(
        ledger_text="YTD LEDGER (2026)\n  NRFI · LOCK   3-1  +1.20u",
    ))
    assert "YTD LEDGER (2026)" in body
    assert "3-1" in body
    # Placeholder must NOT also be rendered when the real one exists.
    assert "no settled picks yet" not in body


def test_render_body_emits_top_board_above_per_side_boards():
    picks = [
        _pick(game_id="BOS @ NYY", tier="ELITE", pct=78.4,
                edge_pp_raw=6.0),
        _pick(game_id="BOS @ NYY", market="YRFI", tier="NO_PLAY",
                pct=21.6),
        _pick(game_id="LAD @ SF",  tier="STRONG", pct=66.0,
                edge_pp_raw=2.0),
        _pick(game_id="LAD @ SF",  market="YRFI", tier="NO_PLAY",
                pct=34.0),
    ]
    body = er.render_body(_minimal_card(picks=picks))
    # Top board appears.
    assert "TOP BOARD" in body
    # And it appears BEFORE the per-side NRFI / YRFI boards.
    top_idx = body.index("TOP BOARD")
    nrfi_idx = body.index("NRFI BOARD")
    assert top_idx < nrfi_idx


def test_render_body_top_board_skipped_when_no_picks():
    body = er.render_body(_minimal_card())
    assert "TOP BOARD" not in body
    assert "(No games on the slate" in body


def test_render_body_keeps_parlay_section_intact():
    parlay_block = "PARLAY CANDIDATES (Special Drops)\n" + "─" * 60
    body = er.render_body(_minimal_card(parlay_text=parlay_block))
    assert "PARLAY CANDIDATES" in body
