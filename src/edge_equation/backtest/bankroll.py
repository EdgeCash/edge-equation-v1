"""
Bankroll simulator for sequential bets.

A BetOutcome captures the stake and the gross payout (0 for a loss, stake for
a push, stake * decimal_odds for a win). Net return is payout - stake.

BankrollSimulator.simulate walks bets in order, tracks the running bankroll,
and reports a BankrollMetrics with ROI, max drawdown (peak-to-trough fraction
of the peak bankroll), and a z-score for the mean per-bet return. Z-score
uses a sample standard deviation (ddof=1); if n <= 1 or stdev == 0 it is 0.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, List
import math


@dataclass(frozen=True)
class BetOutcome:
    """A resolved bet: stake placed and gross payout returned."""
    stake: Decimal
    payout: Decimal

    def net_return(self) -> Decimal:
        return self.payout - self.stake


@dataclass(frozen=True)
class BankrollMetrics:
    """Aggregate performance metrics for a sequence of bets."""
    initial_bankroll: Decimal
    final_bankroll: Decimal
    total_staked: Decimal
    total_net_return: Decimal
    roi: Decimal
    max_drawdown: Decimal
    z_score: Decimal
    n_bets: int
    n_wins: int
    n_losses: int
    n_pushes: int

    def to_dict(self) -> dict:
        return {
            "initial_bankroll": str(self.initial_bankroll),
            "final_bankroll": str(self.final_bankroll),
            "total_staked": str(self.total_staked),
            "total_net_return": str(self.total_net_return),
            "roi": str(self.roi),
            "max_drawdown": str(self.max_drawdown),
            "z_score": str(self.z_score),
            "n_bets": self.n_bets,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "n_pushes": self.n_pushes,
        }


class BankrollSimulator:
    """
    Sequential bet simulator:
    - simulate(initial_bankroll, bets) -> BankrollMetrics
    - Tracks running bankroll, peak, and per-bet net returns.
    - max_drawdown is expressed as a non-negative fraction of peak bankroll.
    - z_score = mean_per_bet / (stdev / sqrt(n)) using ddof=1; 0 if undefined.
    """

    @staticmethod
    def _classify(bet: BetOutcome) -> str:
        if bet.payout == bet.stake:
            return "push"
        if bet.payout > bet.stake:
            return "win"
        return "loss"

    @staticmethod
    def simulate(initial_bankroll: Decimal, bets: Iterable[BetOutcome]) -> BankrollMetrics:
        bets_list: List[BetOutcome] = list(bets)
        bankroll = initial_bankroll
        peak = initial_bankroll
        max_dd = Decimal('0')
        total_staked = Decimal('0')
        total_net = Decimal('0')
        n_wins = n_losses = n_pushes = 0
        net_returns: List[Decimal] = []

        for b in bets_list:
            total_staked += b.stake
            net = b.net_return()
            total_net += net
            bankroll = bankroll + net
            net_returns.append(net)
            cls = BankrollSimulator._classify(b)
            if cls == "win":
                n_wins += 1
            elif cls == "loss":
                n_losses += 1
            else:
                n_pushes += 1

            if bankroll > peak:
                peak = bankroll
            if peak > Decimal('0'):
                dd = (peak - bankroll) / peak
                if dd > max_dd:
                    max_dd = dd

        roi = (total_net / total_staked).quantize(Decimal('0.000001')) if total_staked > Decimal('0') else Decimal('0').quantize(Decimal('0.000001'))

        n = len(bets_list)
        if n <= 1:
            z = Decimal('0').quantize(Decimal('0.000001'))
        else:
            mean_net = sum(net_returns) / Decimal(n)
            variance = sum((x - mean_net) ** 2 for x in net_returns) / Decimal(n - 1)
            stdev = Decimal(str(math.sqrt(float(variance))))
            if stdev == Decimal('0'):
                z = Decimal('0').quantize(Decimal('0.000001'))
            else:
                se = stdev / Decimal(str(math.sqrt(n)))
                z = (mean_net / se).quantize(Decimal('0.000001'))

        return BankrollMetrics(
            initial_bankroll=initial_bankroll,
            final_bankroll=bankroll,
            total_staked=total_staked,
            total_net_return=total_net,
            roi=roi,
            max_drawdown=max_dd.quantize(Decimal('0.000001')),
            z_score=z,
            n_bets=n,
            n_wins=n_wins,
            n_losses=n_losses,
            n_pushes=n_pushes,
        )
