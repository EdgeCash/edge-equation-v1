"""Tests for the parlay block injected into the daily NRFI email.

Pure-Python — uses lightweight fakes for the bridge outputs and the
NRFI store. Verifies the qualifying-leg conversion, the
candidates_text rendering, the ledger section, and the body-level
append into render_body.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from edge_equation.engines.nrfi import email_report as er
from edge_equation.engines.tiering import Tier


@dataclass
class _FakeBridgeOutput:
    """Just enough surface for `_build_parlay_block` to read it."""
    game_id: str
    market_type: str
    fair_prob: float


class _FakeStore:
    """Minimal NRFIStore stand-in."""
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.upserts: list[tuple[str, list[dict]]] = []
        self._rows: list[dict] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql.strip(), tuple(params or ())))

    def upsert(self, table: str, rows) -> int:
        rows = list(rows)
        self.upserts.append((table, rows))
        if table == "parlay_ledger":
            self._rows.extend(rows)
        return len(rows)

    def query_df(self, sql: str, params: tuple = ()):
        if "FROM parlay_ledger" in sql:
            return pd.DataFrame(self._rows)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Qualifying-leg conversion
# ---------------------------------------------------------------------------


def test_build_parlay_block_finds_lock_legs():
    """Two LOCK NRFIs in different games at 85% / 83% → one 2-leg
    candidate that easily clears the 0.68 joint-prob floor."""
    outputs = [
        _FakeBridgeOutput("100001", "NRFI", 0.85),
        _FakeBridgeOutput("100001", "YRFI", 0.15),
        _FakeBridgeOutput("100002", "NRFI", 0.83),
        _FakeBridgeOutput("100002", "YRFI", 0.17),
    ]
    label_map = {"100001": "BOS @ NYY", "100002": "LAD @ SF"}

    candidates_text, ledger_text = er._build_parlay_block(
        outputs, label_map, _FakeStore(),
    )

    assert "PARLAY CANDIDATES" in candidates_text
    assert "PARLAY (2 legs)" in candidates_text
    assert "BOS @ NYY NRFI" in candidates_text
    assert "LAD @ SF NRFI" in candidates_text
    # Ledger is empty until a ticket is recorded.
    assert ledger_text == ""


def test_build_parlay_block_filters_below_strong():
    """A pool of MODERATE-tier legs should yield no candidates."""
    outputs = [
        _FakeBridgeOutput("g1", "NRFI", 0.60),  # MODERATE
        _FakeBridgeOutput("g1", "YRFI", 0.40),
        _FakeBridgeOutput("g2", "NRFI", 0.62),  # MODERATE
        _FakeBridgeOutput("g2", "YRFI", 0.38),
    ]
    candidates_text, ledger_text = er._build_parlay_block(
        outputs, {"g1": "g1", "g2": "g2"}, _FakeStore(),
    )
    assert candidates_text == ""
    assert ledger_text == ""


def test_build_parlay_block_excludes_nrfi_yrfi_same_game():
    """A NRFI + YRFI pairing on the same game must never produce a
    candidate even when both 'sides' technically clear STRONG (which
    is mathematically impossible but defensively tested)."""
    # We fake a degenerate case: market reports both sides at 0.80,
    # which can't physically happen but exercises the filter.
    outputs = [
        _FakeBridgeOutput("g1", "NRFI", 0.80),
        _FakeBridgeOutput("g1", "YRFI", 0.80),
    ]
    candidates_text, ledger_text = er._build_parlay_block(
        outputs, {"g1": "g1"}, _FakeStore(),
    )
    assert candidates_text == ""


def test_build_parlay_block_picks_yrfi_when_thats_the_strong_side():
    """nrfi_prob 0.10 → YRFI side at 0.90 (LOCK). The bridge stores
    `fair_prob` as the side's own prob, so the YRFI row has 0.90.
    Probabilities deliberately above the joint floor so the test is
    not MC-noise sensitive."""
    outputs = [
        _FakeBridgeOutput("g1", "NRFI", 0.10),
        _FakeBridgeOutput("g1", "YRFI", 0.90),  # LOCK on the YRFI side
        _FakeBridgeOutput("g2", "NRFI", 0.88),  # LOCK on the NRFI side
        _FakeBridgeOutput("g2", "YRFI", 0.12),
    ]
    candidates_text, _ = er._build_parlay_block(
        outputs, {"g1": "g1", "g2": "g2"}, _FakeStore(),
    )
    assert "YRFI" in candidates_text
    assert "NRFI" in candidates_text


def test_build_parlay_block_caps_to_top_two_candidates():
    """When many candidates qualify, only the top 2 by EV are rendered.
    Four LOCK NRFIs → 6 two-leg + 4 three-leg = 10 candidates; we show 2."""
    outputs = []
    label_map = {}
    for i in range(4):
        gid = f"10000{i}"
        outputs.append(_FakeBridgeOutput(gid, "NRFI", 0.92 - i * 0.01))
        outputs.append(_FakeBridgeOutput(gid, "YRFI", 0.08 + i * 0.01))
        label_map[gid] = f"team{i*2}@team{i*2+1}"

    candidates_text, _ = er._build_parlay_block(
        outputs, label_map, _FakeStore(),
    )
    assert candidates_text.count("PARLAY (") == 2


# ---------------------------------------------------------------------------
# Ledger rendering integration
# ---------------------------------------------------------------------------


def test_build_parlay_block_renders_ledger_when_tickets_exist():
    """Pre-populate the parlay_ledger via the public API, then verify
    the email block surfaces the ticket in `ledger_text`."""
    from edge_equation.engines.parlay import (
        ParlayLeg, build_parlay_candidates, record_parlay,
    )

    store = _FakeStore()

    # Build a real candidate from real legs and record it.
    legs = [
        ParlayLeg(market_type="NRFI", side="Under 0.5",
                    side_probability=0.85, american_odds=-120,
                    tier=Tier.LOCK, game_id="g1", label="BOS @ NYY NRFI"),
        ParlayLeg(market_type="NRFI", side="Under 0.5",
                    side_probability=0.84, american_odds=-115,
                    tier=Tier.LOCK, game_id="g2", label="LAD @ SF NRFI"),
    ]
    cand = build_parlay_candidates(legs)[0]
    record_parlay(store, cand, parlay_id="opening-week-001")

    # Now run the email block — the candidates_text path may or may
    # not match for our chosen outputs, but the ledger_text must.
    outputs = [
        _FakeBridgeOutput("g1", "NRFI", 0.85),
        _FakeBridgeOutput("g1", "YRFI", 0.15),
        _FakeBridgeOutput("g2", "NRFI", 0.84),
        _FakeBridgeOutput("g2", "YRFI", 0.16),
    ]
    _, ledger_text = er._build_parlay_block(
        outputs, {"g1": "BOS @ NYY", "g2": "LAD @ SF"}, store,
    )

    assert "PARLAY LEDGER" in ledger_text
    assert "tickets recorded" in ledger_text
    assert "1" in ledger_text


# ---------------------------------------------------------------------------
# render_body integration
# ---------------------------------------------------------------------------


def _minimal_card(**overrides):
    """A baseline card dict with everything `render_body` reads."""
    base = {
        "headline": "NRFI Daily — 2026-04-29",
        "subhead": "Facts. Not Feelings.",
        "tagline": "(footer)",
        "engine": "ml",
        "picks": [{
            "market_type": "NRFI",
            "rendered": "[LOCK] BOS @ NYY NRFI 85% λ=0.42",
            "game_id": "BOS @ NYY",
            "pct": 85.0,
        }],
        "ledger_text": "",
        "parlay_text": "",
        "parlay_ledger_text": "",
    }
    base.update(overrides)
    return base


def test_render_body_includes_parlay_section_when_present():
    parlay_block = (
        "PARLAY CANDIDATES (Special Drops)\n"
        + "─" * 60 + "\n"
        + "PARLAY (2 legs)  @ 3.36x (+236)"
    )
    card = _minimal_card(parlay_text=parlay_block)
    body = er.render_body(card)
    assert "PARLAY CANDIDATES" in body
    assert "PARLAY (2 legs)" in body


def test_render_body_includes_parlay_ledger_when_present():
    ledger_block = (
        "PARLAY LEDGER\n"
        + "─" * 60 + "\n"
        + "  tickets recorded   1\n"
        + "  units returned     +2.50u"
    )
    card = _minimal_card(parlay_ledger_text=ledger_block)
    body = er.render_body(card)
    assert "PARLAY LEDGER" in body
    assert "+2.50u" in body


def test_render_body_skips_parlay_section_when_empty():
    card = _minimal_card()  # both parlay strings empty
    body = er.render_body(card)
    assert "PARLAY CANDIDATES" not in body
    assert "PARLAY LEDGER" not in body
