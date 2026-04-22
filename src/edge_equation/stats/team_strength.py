"""
Bradley-Terry team-strength builder.

Blends four deterministic components into a single multiplicative strength
value suitable for ProbabilityCalculator.bradley_terry(strength_home,
strength_away, home_adv):

    1. Pythagorean expectation over a recent window
       WR_pyth = RS^x / (RS^x + RA^x)        (x = sport.pythagorean_exponent)
       strength_pyth = WR_pyth / (1 - WR_pyth)   (odds form)

    2. Decay-weighted recent form (win / loss / draw)
       weight_i = lambda^i for i-th most-recent game
       WR_form  = sum(w_i * outcome_i) / sum(w_i)
       strength_form = WR_form / (1 - WR_form)

    3. Elo rating (opponent-adjusted)
       strength_elo = exp((rating - 1500) / 400)

    4. Pitching adjustment (baseball-family only)
       starter_weight = 1 - bullpen_weight
       pitching_log = starter_weight * (-log(fip_starter / league_fip))
                    + bullpen_weight * (-log(fip_bullpen / league_fip))
       strength_pitch = exp(pitching_log)
       (Lower FIP => better pitching => higher strength.)

Final blend in log-strength space, weighted by SPORT_CONFIG["strength_blend"]:

       log_strength = sum( w_i * log(s_i) ) over available components
       strength     = exp(log_strength)

Missing components (no Elo, no pitching inputs, zero games) get weight 0;
remaining weights renormalize to 1 so the result is always well-defined.
The final strength is clamped to [0.1, 10.0] so downstream Bradley-Terry
probabilities stay in a sane range (roughly [0.01, 0.99] at equal odds).

Everything below is pure Decimal + stdlib math. No RNG, no training.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
import math

from edge_equation.config.sport_config import SportConfig
from edge_equation.stats.elo import EloRatings, STARTING_RATING
from edge_equation.stats.results import GameResult


STRENGTH_FLOOR = Decimal('0.10')
STRENGTH_CEIL = Decimal('10.00')
NEUTRAL_STRENGTH = Decimal('1.000000')


@dataclass(frozen=True)
class PitchingInputs:
    """
    Optional baseball-family pitching adjustment inputs. All three are
    in the same units (ERA or FIP -- the builder treats them as
    ratio-invariant, so long as team and league values are comparable).
    """
    starter_fip: Decimal
    bullpen_fip: Decimal
    league_fip: Decimal

    def to_dict(self) -> dict:
        return {
            "starter_fip": str(self.starter_fip),
            "bullpen_fip": str(self.bullpen_fip),
            "league_fip": str(self.league_fip),
        }


@dataclass(frozen=True)
class TeamStrengthComponents:
    """Per-component strengths feeding the final blend. NEUTRAL_STRENGTH (1.0)
    marks a component that was requested but unavailable at compute time."""
    pyth: Optional[Decimal]
    form: Optional[Decimal]
    elo: Optional[Decimal]
    pitching: Optional[Decimal]

    def to_dict(self) -> dict:
        return {
            "pyth": str(self.pyth) if self.pyth is not None else None,
            "form": str(self.form) if self.form is not None else None,
            "elo": str(self.elo) if self.elo is not None else None,
            "pitching": str(self.pitching) if self.pitching is not None else None,
        }


@dataclass(frozen=True)
class TeamStrength:
    """Final blended strength + full audit trail."""
    team: str
    league: str
    strength: Decimal
    components: TeamStrengthComponents
    effective_weights: Dict[str, Decimal] = field(default_factory=dict)
    games_used: int = 0

    def to_dict(self) -> dict:
        return {
            "team": self.team,
            "league": self.league,
            "strength": str(self.strength),
            "components": self.components.to_dict(),
            "effective_weights": {k: str(v) for k, v in self.effective_weights.items()},
            "games_used": self.games_used,
        }


class TeamStrengthBuilder:
    """
    Component helpers + the top-level build():
    - pythagorean_strength(rs, ra, exponent)          -> Decimal
    - form_strength(games, team, decay_lambda, window) -> (Decimal | None, games_used)
    - elo_strength(team, elo_ratings)                 -> Decimal | None
    - pitching_strength(pitching, bullpen_weight)     -> Decimal | None
    - build(team, league, results, elo=None, pitching=None, bullpen_weight=None)
        -> TeamStrength
    """

    # -------------------------------------------------- utilities

    @staticmethod
    def _wr_to_strength(wr: Decimal) -> Decimal:
        """Odds-form conversion: WR / (1 - WR). Clamp at the ends to stay finite."""
        # Avoid division by zero / infinity for degenerate wr values.
        floor_wr = Decimal('0.01')
        ceil_wr = Decimal('0.99')
        w = wr
        if w < floor_wr:
            w = floor_wr
        elif w > ceil_wr:
            w = ceil_wr
        return (w / (Decimal('1') - w)).quantize(Decimal('0.000001'))

    @staticmethod
    def _clamp(value: Decimal, floor: Decimal = STRENGTH_FLOOR, ceil: Decimal = STRENGTH_CEIL) -> Decimal:
        if value < floor:
            return floor
        if value > ceil:
            return ceil
        return value

    # -------------------------------------------------- components

    @staticmethod
    def pythagorean_strength(rs: Decimal, ra: Decimal, exponent: Decimal) -> Optional[Decimal]:
        """
        Pythagorean-expectation strength from total runs scored (rs) vs total
        runs allowed (ra) over a recent window. Returns None if rs+ra == 0
        (no data), otherwise strength in (0, infinity).
        """
        if rs < Decimal('0') or ra < Decimal('0'):
            raise ValueError(f"rs/ra must be non-negative, got {rs}/{ra}")
        total = rs + ra
        if total == Decimal('0'):
            return None
        rs_f = float(rs)
        ra_f = float(ra)
        x = float(exponent)
        # RS^x / (RS^x + RA^x)
        rs_pow = rs_f ** x if rs_f > 0 else 0.0
        ra_pow = ra_f ** x if ra_f > 0 else 0.0
        if rs_pow + ra_pow == 0.0:
            return None
        wr = Decimal(str(rs_pow / (rs_pow + ra_pow)))
        return TeamStrengthBuilder._wr_to_strength(wr)

    @staticmethod
    def form_strength(
        games: Iterable[GameResult],
        team: str,
        decay_lambda: Decimal,
        window: int,
    ) -> tuple:
        """
        Decay-weighted win rate over the team's last `window` games. Draws
        count as 0.5. Returns (strength_or_None, games_used).
        """
        # Filter to games the team played in, sort descending by start_time
        team_games: List[GameResult] = sorted(
            [g for g in games if team in (g.home_team, g.away_team)],
            key=lambda g: g.start_time,
            reverse=True,
        )[: max(0, int(window))]
        if not team_games:
            return (None, 0)

        lam = Decimal(str(decay_lambda))
        if lam <= Decimal('0') or lam > Decimal('1'):
            raise ValueError(f"decay_lambda must be in (0, 1], got {lam}")

        total_weight = Decimal('0')
        weighted_outcome = Decimal('0')
        for i, g in enumerate(team_games):
            w = lam ** i  # most-recent game i=0 carries weight 1.0
            total_weight += w
            if g.is_draw():
                weighted_outcome += w * Decimal('0.5')
                continue
            team_won = (
                (g.home_team == team and g.home_won())
                or (g.away_team == team and not g.home_won())
            )
            if team_won:
                weighted_outcome += w
        if total_weight == Decimal('0'):
            return (None, len(team_games))
        wr = weighted_outcome / total_weight
        return (TeamStrengthBuilder._wr_to_strength(wr), len(team_games))

    @staticmethod
    def elo_strength(team: str, elo: Optional[EloRatings]) -> Optional[Decimal]:
        """Map Elo to multiplicative BT strength: exp((rating - 1500) / 400)."""
        if elo is None:
            return None
        rating = elo.ratings.get(team)
        if rating is None:
            return None  # untouched team -> no Elo signal (not the starting 1500)
        s = math.exp((float(rating) - float(STARTING_RATING)) / 400.0)
        return Decimal(str(s)).quantize(Decimal('0.000001'))

    @staticmethod
    def pitching_strength(
        pitching: Optional[PitchingInputs],
        bullpen_weight: Optional[Decimal],
    ) -> Optional[Decimal]:
        """
        Pitching-adjusted strength multiplier. Requires team starter_fip +
        bullpen_fip + league_fip and a bullpen_weight share (the remainder
        goes to the starter). Returns None if any input is missing.
        """
        if pitching is None:
            return None
        if bullpen_weight is None:
            return None
        bw = bullpen_weight if isinstance(bullpen_weight, Decimal) else Decimal(str(bullpen_weight))
        if bw < Decimal('0') or bw > Decimal('1'):
            raise ValueError(f"bullpen_weight must be in [0, 1], got {bw}")
        starter_w = Decimal('1') - bw
        if pitching.league_fip <= Decimal('0'):
            raise ValueError("league_fip must be > 0")
        if pitching.starter_fip <= Decimal('0') or pitching.bullpen_fip <= Decimal('0'):
            raise ValueError("starter_fip and bullpen_fip must be > 0")

        # Better pitching = lower FIP ratio = positive log-strength term.
        starter_log = -math.log(float(pitching.starter_fip) / float(pitching.league_fip))
        bullpen_log = -math.log(float(pitching.bullpen_fip) / float(pitching.league_fip))
        combined = float(starter_w) * starter_log + float(bw) * bullpen_log
        return Decimal(str(math.exp(combined))).quantize(Decimal('0.000001'))

    # -------------------------------------------------- build

    @staticmethod
    def build(
        team: str,
        league: str,
        results: List[GameResult],
        elo: Optional[EloRatings] = None,
        pitching: Optional[PitchingInputs] = None,
        bullpen_weight: Optional[Decimal] = None,
    ) -> TeamStrength:
        cfg_blend = SportConfig.strength_blend(league)
        exponent = SportConfig.pythagorean_exponent(league)
        decay_lambda = SportConfig.decay_lambda(league)
        window = SportConfig.form_window_games(league)
        if bullpen_weight is None:
            bullpen_weight = SportConfig.bullpen_weight(league)

        # ---------- component values
        scoped = [g for g in results if g.league == league]
        team_games = [g for g in scoped if team in (g.home_team, g.away_team)]
        recent = sorted(team_games, key=lambda g: g.start_time, reverse=True)[:window]
        rs = Decimal(sum(
            (g.home_score if g.home_team == team else g.away_score) for g in recent
        ))
        ra = Decimal(sum(
            (g.away_score if g.home_team == team else g.home_score) for g in recent
        ))
        pyth = TeamStrengthBuilder.pythagorean_strength(rs, ra, exponent)
        form, games_used = TeamStrengthBuilder.form_strength(scoped, team, decay_lambda, window)
        elo_s = TeamStrengthBuilder.elo_strength(team, elo)
        pitch_s = TeamStrengthBuilder.pitching_strength(pitching, bullpen_weight)

        components = TeamStrengthComponents(
            pyth=pyth, form=form, elo=elo_s, pitching=pitch_s,
        )

        # ---------- weighted geometric mean in log-space
        # Skip components that are None; renormalize remaining weights.
        pairs = [
            ("pyth", pyth, cfg_blend.get("pyth", Decimal('0'))),
            ("form", form, cfg_blend.get("form", Decimal('0'))),
            ("elo", elo_s, cfg_blend.get("elo", Decimal('0'))),
            ("pitching", pitch_s, cfg_blend.get("pitching", Decimal('0'))),
        ]
        active = [(n, s, w) for (n, s, w) in pairs if s is not None and w > Decimal('0')]
        if not active:
            return TeamStrength(
                team=team, league=league,
                strength=NEUTRAL_STRENGTH,
                components=components,
                effective_weights={},
                games_used=games_used,
            )
        total_w = sum(w for (_, _, w) in active)
        effective: Dict[str, Decimal] = {}
        log_strength = 0.0
        for (name, s, w) in active:
            w_eff = (w / total_w).quantize(Decimal('0.000001'))
            effective[name] = w_eff
            log_strength += float(w_eff) * math.log(max(float(s), 1e-9))
        blended = Decimal(str(math.exp(log_strength))).quantize(Decimal('0.000001'))
        blended = TeamStrengthBuilder._clamp(blended)

        return TeamStrength(
            team=team, league=league,
            strength=blended,
            components=components,
            effective_weights=effective,
            games_used=games_used,
        )
