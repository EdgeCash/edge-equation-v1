"""Season Ledger footer + LedgerStore computation tests."""
from decimal import Decimal
import pytest

from edge_equation.compliance import LEDGER_FOOTER_RE
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.ledger import (
    DEFAULT_UNIT_STAKE,
    LedgerStats,
    LedgerStore,
    format_ledger_footer,
    _american_to_net_decimal,
)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


def _seed_slate(conn, slate_id="s1"):
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id, generated_at="2026-04-20T09:00",
        sport="MLB", card_type="daily_edge",
    ))


def _seed_pick(conn, slate_id, grade="A", realization=100, odds=-132,
               selection="BOS", game_id="G1"):
    PickStore.insert(conn, Pick(
        sport="MLB", market_type="ML", selection=selection,
        line=Line(odds=odds), grade=grade, realization=realization,
        fair_prob=Decimal('0.55'),
        game_id=game_id,
    ), slate_id=slate_id)


# -------------------------------------------- footer template


def test_format_footer_positive():
    stats = LedgerStats(
        wins=68, losses=49, pushes=3,
        units_net=Decimal('8.45'), roi_pct=Decimal('7.0'),
        total_plays=120,
    )
    assert format_ledger_footer(stats) == (
        "Season Ledger: 68-49-3 +8.45 units +7.0 ROI | "
        "Bet within your means. Problem? Call 1-800-GAMBLER."
    )


def test_format_footer_negative_units_and_roi():
    stats = LedgerStats(
        wins=12, losses=15, pushes=1,
        units_net=Decimal('-3.20'), roi_pct=Decimal('-1.8'),
        total_plays=28,
    )
    footer = format_ledger_footer(stats)
    assert "-3.20 units" in footer
    assert "-1.8 ROI" in footer


def test_format_footer_matches_regex():
    stats = LedgerStats(
        wins=0, losses=0, pushes=0,
        units_net=Decimal('0.00'), roi_pct=Decimal('0.0'),
        total_plays=0,
    )
    assert LEDGER_FOOTER_RE.search(format_ledger_footer(stats)) is not None


# -------------------------------------------- american-to-net helper


def test_american_to_net_decimal_favorite():
    # -200 pays 50 units on 100 staked -> 0.5
    assert _american_to_net_decimal(-200) == Decimal('0.5')


def test_american_to_net_decimal_underdog():
    # +150 pays 150 on 100 staked -> 1.5
    assert _american_to_net_decimal(150) == Decimal('1.5')


# -------------------------------------------- LedgerStore.compute


def test_compute_empty_db_zeroed(conn):
    stats = LedgerStore.compute(conn)
    assert stats.wins == 0
    assert stats.losses == 0
    assert stats.pushes == 0
    assert stats.units_net == Decimal('0.00')
    assert stats.roi_pct == Decimal('0.0')


def test_compute_counts_wins_losses_pushes(conn):
    _seed_slate(conn)
    # 2 wins (different odds), 1 loss, 1 push, 1 void (excluded)
    _seed_pick(conn, "s1", realization=100, odds=-110, game_id="G1")
    _seed_pick(conn, "s1", realization=100, odds=150,  game_id="G2")
    _seed_pick(conn, "s1", realization=0,   odds=-110, game_id="G3")
    _seed_pick(conn, "s1", realization=50,  odds=-110, game_id="G4")
    _seed_pick(conn, "s1", realization=-1,  odds=-110, game_id="G5")   # void

    stats = LedgerStore.compute(conn)
    assert stats.wins == 2
    assert stats.losses == 1
    assert stats.pushes == 1


def test_compute_units_net_accounts_for_price(conn):
    _seed_slate(conn)
    # 1 win at +150 -> +1.5 units
    _seed_pick(conn, "s1", realization=100, odds=150, game_id="G1")
    # 1 loss -> -1.0 unit
    _seed_pick(conn, "s1", realization=0, odds=-110, game_id="G2")
    stats = LedgerStore.compute(conn)
    assert stats.units_net == Decimal('0.50')


def test_compute_roi_percent(conn):
    _seed_slate(conn)
    # 3 wins at -110 -> each returns 0.909 units profit. 3 * 0.909 ~ 2.727
    for i in range(3):
        _seed_pick(conn, "s1", realization=100, odds=-110, game_id=f"GW{i}")
    # Total staked = 3 * 1 = 3. ROI = 2.727 / 3 * 100 ~= 90.9%
    stats = LedgerStore.compute(conn)
    assert Decimal('90') < stats.roi_pct < Decimal('92')


def test_compute_filtered_by_sport(conn):
    _seed_slate(conn)
    _seed_pick(conn, "s1", realization=100, odds=-110, game_id="G1")
    # Also seed an NHL slate+pick
    SlateStore.insert(conn, SlateRecord(
        slate_id="s_nhl", generated_at="2026-04-20T19:00",
        sport="NHL", card_type="evening_edge",
    ))
    PickStore.insert(conn, Pick(
        sport="NHL", market_type="ML", selection="PIT",
        line=Line(odds=-120), grade="A", realization=0,
        fair_prob=Decimal('0.5'),
        game_id="H1",
    ), slate_id="s_nhl")

    mlb_stats = LedgerStore.compute(conn, sport="MLB")
    assert mlb_stats.total_plays == 1
    assert mlb_stats.wins == 1
    nhl_stats = LedgerStore.compute(conn, sport="NHL")
    assert nhl_stats.total_plays == 1
    assert nhl_stats.losses == 1


def test_compute_ignores_unsettled_picks(conn):
    _seed_slate(conn)
    # realization default 47 (C tier) -> unsettled forecast, excluded
    _seed_pick(conn, "s1", realization=47, odds=-110, game_id="G1")
    stats = LedgerStore.compute(conn)
    assert stats.total_plays == 0


def test_default_unit_stake_is_one():
    assert DEFAULT_UNIT_STAKE == Decimal('1.00')


def test_ledger_stats_frozen():
    stats = LedgerStats(
        wins=1, losses=0, pushes=0,
        units_net=Decimal('0.91'), roi_pct=Decimal('90.9'),
        total_plays=1,
    )
    with pytest.raises(Exception):
        stats.wins = 99
