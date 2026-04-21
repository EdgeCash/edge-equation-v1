import pytest
from decimal import Decimal

from edge_equation.backtest.bankroll import (
    BetOutcome,
    BankrollMetrics,
    BankrollSimulator,
)


def test_empty_bets_zero_metrics():
    m = BankrollSimulator.simulate(Decimal('100'), [])
    assert m.n_bets == 0
    assert m.final_bankroll == Decimal('100')
    assert m.total_staked == Decimal('0')
    assert m.roi == Decimal('0').quantize(Decimal('0.000001'))


def test_single_win_updates_bankroll():
    bets = [BetOutcome(stake=Decimal('10'), payout=Decimal('20'))]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    assert m.final_bankroll == Decimal('110')
    assert m.n_wins == 1
    assert m.n_losses == 0
    assert m.n_pushes == 0


def test_single_loss_updates_bankroll():
    bets = [BetOutcome(stake=Decimal('10'), payout=Decimal('0'))]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    assert m.final_bankroll == Decimal('90')
    assert m.n_wins == 0
    assert m.n_losses == 1


def test_push_no_change():
    bets = [BetOutcome(stake=Decimal('10'), payout=Decimal('10'))]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    assert m.final_bankroll == Decimal('100')
    assert m.n_pushes == 1


def test_roi_calculation():
    # 3 bets: win $10 on $10 stake, lose $10 stake, win $20 on $20 stake
    bets = [
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),
        BetOutcome(stake=Decimal('10'), payout=Decimal('0')),
        BetOutcome(stake=Decimal('20'), payout=Decimal('40')),
    ]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    # staked = 40, net = +10 -10 +20 = 20; roi = 20/40 = 0.5
    assert m.total_staked == Decimal('40')
    assert m.total_net_return == Decimal('20')
    assert m.roi == Decimal('0.500000')


def test_max_drawdown_zero_when_monotonic_gain():
    bets = [
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),
        BetOutcome(stake=Decimal('10'), payout=Decimal('25')),
    ]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    assert m.max_drawdown == Decimal('0').quantize(Decimal('0.000001'))


def test_max_drawdown_tracks_peak_to_trough():
    # bankroll path: 100 -> 120 (win) -> 100 (loss 20) -> 80 (loss 20)
    # peak = 120, trough = 80; dd = (120 - 80) / 120 = 0.333...
    bets = [
        BetOutcome(stake=Decimal('20'), payout=Decimal('40')),  # +20
        BetOutcome(stake=Decimal('20'), payout=Decimal('0')),   # -20
        BetOutcome(stake=Decimal('20'), payout=Decimal('0')),   # -20
    ]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    expected = (Decimal('40') / Decimal('120')).quantize(Decimal('0.000001'))
    assert m.max_drawdown == expected


def test_z_score_zero_single_bet():
    bets = [BetOutcome(stake=Decimal('10'), payout=Decimal('20'))]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    assert m.z_score == Decimal('0').quantize(Decimal('0.000001'))


def test_z_score_zero_all_equal_returns():
    bets = [
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),
    ]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    # stdev = 0; z = 0
    assert m.z_score == Decimal('0').quantize(Decimal('0.000001'))


def test_z_score_positive_for_profitable_series():
    bets = [
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),  # +10
        BetOutcome(stake=Decimal('10'), payout=Decimal('0')),   # -10
        BetOutcome(stake=Decimal('10'), payout=Decimal('25')),  # +15
        BetOutcome(stake=Decimal('10'), payout=Decimal('20')),  # +10
        BetOutcome(stake=Decimal('10'), payout=Decimal('30')),  # +20
    ]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    assert m.z_score > Decimal('0')


def test_metrics_final_bankroll_matches_sum():
    bets = [
        BetOutcome(stake=Decimal('25'), payout=Decimal('0')),
        BetOutcome(stake=Decimal('25'), payout=Decimal('50')),
        BetOutcome(stake=Decimal('25'), payout=Decimal('75')),
    ]
    m = BankrollSimulator.simulate(Decimal('500'), bets)
    # 500 -25 +25 +50 = 550
    assert m.final_bankroll == Decimal('550')


def test_bet_outcome_frozen():
    b = BetOutcome(stake=Decimal('10'), payout=Decimal('20'))
    with pytest.raises(Exception):
        b.stake = Decimal('999')


def test_metrics_to_dict_has_strings():
    bets = [BetOutcome(stake=Decimal('10'), payout=Decimal('20'))]
    m = BankrollSimulator.simulate(Decimal('100'), bets)
    d = m.to_dict()
    assert isinstance(d["roi"], str)
    assert isinstance(d["max_drawdown"], str)
    assert d["n_wins"] == 1


def test_net_return_helper():
    b = BetOutcome(stake=Decimal('10'), payout=Decimal('25'))
    assert b.net_return() == Decimal('15')
