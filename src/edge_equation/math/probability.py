import os
from decimal import Decimal
from .stats import DeterministicStats
from edge_equation.config.sport_config import SPORT_CONFIG


def _debug_enabled() -> bool:
    return os.getenv("DEBUG") == "1"


class ProbabilityCalculator:
    """
    Core fair-value math:
    - ML: Bradley-Terry + weighted universal_sum (clamped), then prob clamped to [0.01, 0.99]
    - Totals: league_baseline_total * (off_env * def_env * pace) + Dixon-Coles adj
    - Props: base_rate * (1 + prop_weight * clamped_universal_sum), with multiplier clamped to [0.75, 1.25]
    """

    @staticmethod
    def bradley_terry(strength_home: float, strength_away: float, home_adv: float) -> Decimal:
        import math
        home = strength_home * math.exp(home_adv)
        away = strength_away
        prob = home / (home + away)
        return Decimal(str(prob))

    @staticmethod
    def _get_weights_and_baseline(sport: str):
        cfg = SPORT_CONFIG[sport]
        return (
            cfg["ml_universal_weight"],
            cfg["prop_universal_weight"],
            cfg["league_baseline_total"],
        )

    @staticmethod
    def expected_total(sport: str, off_env: float, def_env: float, pace: float, dixon_coles_adj: float) -> Decimal:
        _, _, baseline = ProbabilityCalculator._get_weights_and_baseline(sport)
        env_factor = Decimal(str(off_env)) * Decimal(str(def_env)) * Decimal(str(pace))
        total = baseline * env_factor + Decimal(str(dixon_coles_adj))
        return total

    @staticmethod
    def expected_prop_rate(base_rate: float, raw_universal_sum: Decimal, prop_weight: Decimal) -> Decimal:
        # Apply weight to universal_sum, then clamp multiplier to [0.75, 1.25]
        weighted_univ = raw_universal_sum * prop_weight
        multiplier = DeterministicStats.clamp_prop_multiplier(weighted_univ)
        return Decimal(str(base_rate)) * multiplier

    @staticmethod
    def calculate_fair_value(market_type: str, sport: str, inputs: dict, universal_features: dict) -> dict:
        raw_univ = DeterministicStats.compute_universal_sum(universal_features)
        ml_weight, prop_weight, _ = ProbabilityCalculator._get_weights_and_baseline(sport)

        # ML: BT + weighted universal_sum (clamped), then prob clamped to [0.01, 0.99]
        if market_type == "ML":
            base_prob = ProbabilityCalculator.bradley_terry(
                inputs["strength_home"],
                inputs["strength_away"],
                inputs.get("home_adv", 0.115),
            )
            clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
            fair_prob = base_prob + clamped_univ * ml_weight
            if fair_prob < Decimal('0.01'):
                fair_prob = Decimal('0.01')
            if fair_prob > Decimal('0.99'):
                fair_prob = Decimal('0.99')
            fair_prob = fair_prob.quantize(Decimal('0.000001'))
            result = {
                "fair_prob": fair_prob,
                "raw_universal_sum": raw_univ,
                "clamped_universal_sum": clamped_univ,
            }
            if _debug_enabled():
                game_id = inputs.get("game_id", "?")
                print(f"[DEBUG] Market {market_type} for {game_id}: "
                      f"fair_prob={result.get('fair_prob')}, "
                      f"edge={result.get('edge')}")
            return result

        # Spread / Run_Line / Puck_Line: BT home-centric prob plus a
        # per-sport line-to-probability conversion. The old shared
        # denominator (baseline * 0.5) over-corrected low-scoring sports
        # (NHL/MLB) and under-corrected nothing in particular; see the
        # spread_line_weight key in SPORT_CONFIG for tuning rationale.
        if market_type in ("Spread", "Run_Line", "Puck_Line"):
            base_prob = ProbabilityCalculator.bradley_terry(
                inputs["strength_home"],
                inputs["strength_away"],
                inputs.get("home_adv", 0.115),
            )
            cfg = SPORT_CONFIG.get(sport, {})
            # Fall back to the pre-calibration denominator shape when the
            # sport config predates this key, so adding a new sport later
            # doesn't crash the engine -- it just grades poorly until tuned.
            spread_weight = cfg.get("spread_line_weight")
            raw_line = inputs.get("line")
            if _debug_enabled():
                print(f"[DEBUG] {market_type} received line={raw_line} "
                      f"(spread_line_weight={spread_weight})")
            line_val = Decimal(str(raw_line)) if raw_line is not None else Decimal('0')
            # Line is expressed from the home side (negative = home favored).
            # Covering a negative line is HARDER than winning outright, so a
            # negative line must REDUCE home_prob: adjustment carries the
            # same sign as the line itself.
            if spread_weight is not None:
                line_adj = line_val * spread_weight
            else:
                _, _, baseline = ProbabilityCalculator._get_weights_and_baseline(sport)
                line_adj = line_val / (baseline * Decimal('0.5'))
            clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
            fair_prob = base_prob + line_adj + clamped_univ * ml_weight
            if fair_prob < Decimal('0.01'):
                fair_prob = Decimal('0.01')
            if fair_prob > Decimal('0.99'):
                fair_prob = Decimal('0.99')
            fair_prob = fair_prob.quantize(Decimal('0.000001'))
            result = {
                "fair_prob": fair_prob,
                "raw_universal_sum": raw_univ,
                "clamped_universal_sum": clamped_univ,
            }
            if _debug_enabled():
                game_id = inputs.get("game_id", "?")
                print(f"[DEBUG] Market {market_type} for {game_id}: "
                      f"fair_prob={result.get('fair_prob')}, "
                      f"edge={result.get('edge')}")
            return result

        # Totals: league_baseline_total * env_factor + DC adj, then 2-decimal rounding
        if market_type in ["Total", "Game_Total"]:
            dixon_coles_adj = inputs.get("dixon_coles_adj", 0.0)
            total = ProbabilityCalculator.expected_total(
                sport,
                inputs["off_env"],
                inputs["def_env"],
                inputs["pace"],
                dixon_coles_adj,
            )
            total = total.quantize(Decimal('0.01'))
            return {
                "expected_total": total,
                "raw_universal_sum": raw_univ,
            }

        # Rate-based props: HR, K, yards, points, rebounds, assists, SOG
        if market_type in [
            "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
            "Points", "Rebounds", "Assists", "SOG"
        ]:
            base_rate = inputs["rate"]
            adjusted = ProbabilityCalculator.expected_prop_rate(base_rate, raw_univ, prop_weight)
            adjusted = adjusted.quantize(Decimal('0.01'))
            return {
                "expected_value": adjusted,
                "raw_universal_sum": raw_univ,
            }

        # BTTS: Poisson-based, then universal_sum impact on prob (clamped)
        if market_type == "BTTS":
            import math
            home_lambda = inputs.get("home_lambda", 1.2)
            away_lambda = inputs.get("away_lambda", 1.1)
            p0_home = math.exp(-home_lambda)
            p0_away = math.exp(-away_lambda)
            base_prob = 1 - (p0_home * p0_away)
            clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
            fair_prob = Decimal(str(base_prob)) + clamped_univ * ml_weight
            if fair_prob < Decimal('0.01'):
                fair_prob = Decimal('0.01')
            if fair_prob > Decimal('0.99'):
                fair_prob = Decimal('0.99')
            fair_prob = fair_prob.quantize(Decimal('0.000001'))
            return {
                "fair_prob": fair_prob,
                "raw_universal_sum": raw_univ,
                "clamped_universal_sum": clamped_univ,
            }

        # NRFI / YRFI: first-inning Poisson. Reuses the BTTS-style lambdas,
        # scaled down to a single inning when explicit first-inning lambdas
        # weren't provided. NRFI = P(no runs in inning 1 by either team);
        # YRFI = 1 - NRFI.
        if market_type in ("NRFI", "YRFI"):
            import math
            home_lambda = inputs.get("home_lambda", 1.2)
            away_lambda = inputs.get("away_lambda", 1.1)
            home_first = inputs.get("home_first_inning_lambda", home_lambda / 9.0)
            away_first = inputs.get("away_first_inning_lambda", away_lambda / 9.0)
            nrfi_prob = math.exp(-home_first) * math.exp(-away_first)
            base_prob = nrfi_prob if market_type == "NRFI" else 1.0 - nrfi_prob
            clamped_univ = DeterministicStats.clamp_universal_prob(raw_univ)
            fair_prob = Decimal(str(base_prob)) + clamped_univ * ml_weight
            if fair_prob < Decimal('0.01'):
                fair_prob = Decimal('0.01')
            if fair_prob > Decimal('0.99'):
                fair_prob = Decimal('0.99')
            fair_prob = fair_prob.quantize(Decimal('0.000001'))
            result = {
                "fair_prob": fair_prob,
                "raw_universal_sum": raw_univ,
                "clamped_universal_sum": clamped_univ,
            }
            if _debug_enabled():
                game_id = inputs.get("game_id", "?")
                print(f"[DEBUG] Market {market_type} for {game_id}: "
                      f"fair_prob={result.get('fair_prob')}, "
                      f"edge={result.get('edge')}")
            return result

        raise ValueError(f"Unsupported market_type: {market_type} for sport {sport}")
