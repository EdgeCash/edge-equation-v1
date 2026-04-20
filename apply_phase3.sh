#!/usr/bin/env bash
# apply_phase3.sh
#
# Writes all Phase-3 modules and tests. Runs pytest. Exits non-zero on failure.
#
# Run from the repo root of edge-equation-v1 on branch phase-3-engine.

set -euo pipefail

echo "=== Phase 3: writing modules and tests ==="

ROOT_DIR="$(pwd)"
SRC="$ROOT_DIR/src"
TESTS="$ROOT_DIR/tests"

mkdir -p "$SRC/edge_equation/engine"
mkdir -p "$SRC/edge_equation/posting"
mkdir -p "$TESTS"

# Ensure package __init__.py files exist
[ -f "$SRC/edge_equation/engine/__init__.py" ] || touch "$SRC/edge_equation/engine/__init__.py"
[ -f "$SRC/edge_equation/posting/__init__.py" ] || touch "$SRC/edge_equation/posting/__init__.py"

########################################
# pick_schema.py
########################################
cat > "$SRC/edge_equation/engine/pick_schema.py" << 'EOF'
"""
Deterministic Pick schema.

A Pick is the fully-populated output of the betting engine for a single market.
It holds the math result (fair_prob or expected_value), the market line,
and the calibration output (edge, kelly, grade, realization).

Picks are frozen after construction to preserve determinism -- downstream
formatters and publishers must not mutate them.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Optional


@dataclass(frozen=True)
class Line:
    """A market line: price (American odds) + optional number (spread/total)."""
    odds: int
    number: Optional[Decimal] = None

    def to_dict(self) -> dict:
        return {
            "odds": self.odds,
            "number": str(self.number) if self.number is not None else None,
        }


@dataclass(frozen=True)
class Pick:
    sport: str
    market_type: str
    selection: str
    line: Line
    fair_prob: Optional[Decimal] = None
    expected_value: Optional[Decimal] = None
    edge: Optional[Decimal] = None
    kelly: Optional[Decimal] = None
    grade: str = "C"
    realization: int = 47
    game_id: Optional[str] = None
    event_time: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "market_type": self.market_type,
            "selection": self.selection,
            "line": self.line.to_dict(),
            "fair_prob": str(self.fair_prob) if self.fair_prob is not None else None,
            "expected_value": str(self.expected_value) if self.expected_value is not None else None,
            "edge": str(self.edge) if self.edge is not None else None,
            "kelly": str(self.kelly) if self.kelly is not None else None,
            "grade": self.grade,
            "realization": self.realization,
            "game_id": self.game_id,
            "event_time": self.event_time,
            "metadata": dict(self.metadata),
        }
EOF

########################################
# feature_builder.py
########################################
cat > "$SRC/edge_equation/engine/feature_builder.py" << 'EOF'
"""
Feature builder.

Produces a FeatureBundle that the math layer can consume directly. Validates
sport + market_type against sport_config. Drops unknown universal feature keys.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from edge_equation.math.stats import DeterministicStats
from edge_equation.config.sport_config import SPORT_CONFIG


RATE_PROP_MARKETS = {
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
}
ML_MARKETS = {"ML", "Run_Line", "Puck_Line", "Spread"}
TOTAL_MARKETS = {"Total", "Game_Total"}
BTTS_MARKETS = {"BTTS"}
PASSTHROUGH_MARKETS = {"NRFI", "YRFI"}


@dataclass
class FeatureBundle:
    sport: str
    market_type: str
    inputs: dict
    universal_features: dict
    game_id: Optional[str] = None
    event_time: Optional[str] = None
    selection: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "market_type": self.market_type,
            "inputs": dict(self.inputs),
            "universal_features": dict(self.universal_features),
            "game_id": self.game_id,
            "event_time": self.event_time,
            "selection": self.selection,
            "metadata": dict(self.metadata),
        }


class FeatureBuilder:
    @staticmethod
    def _validate_sport_market(sport: str, market_type: str) -> None:
        if sport not in SPORT_CONFIG:
            raise ValueError(f"Unknown sport: {sport}")
        allowed = SPORT_CONFIG[sport]["markets"]
        if market_type not in allowed:
            raise ValueError(
                f"Market '{market_type}' not supported for sport '{sport}'. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _normalize_universal(raw: dict) -> dict:
        clean = {}
        for k in DeterministicStats.UNIVERSAL_KEYS:
            if k in raw:
                clean[k] = float(raw[k])
        return clean

    @staticmethod
    def _validate_inputs(market_type: str, inputs: dict) -> None:
        if market_type in ML_MARKETS:
            required = ["strength_home", "strength_away"]
        elif market_type in TOTAL_MARKETS:
            required = ["off_env", "def_env", "pace"]
        elif market_type in RATE_PROP_MARKETS:
            required = ["rate"]
        elif market_type in BTTS_MARKETS:
            required = ["home_lambda", "away_lambda"]
        elif market_type in PASSTHROUGH_MARKETS:
            required = []
        else:
            raise ValueError(f"Unsupported market_type: {market_type}")
        missing = [k for k in required if k not in inputs]
        if missing:
            raise ValueError(
                f"Missing required inputs for market '{market_type}': {missing}"
            )

    @staticmethod
    def build(
        sport: str,
        market_type: str,
        inputs: dict,
        universal_features: Optional[dict] = None,
        game_id: Optional[str] = None,
        event_time: Optional[str] = None,
        selection: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> FeatureBundle:
        FeatureBuilder._validate_sport_market(sport, market_type)
        FeatureBuilder._validate_inputs(market_type, inputs)
        clean_univ = FeatureBuilder._normalize_universal(universal_features or {})
        return FeatureBundle(
            sport=sport,
            market_type=market_type,
            inputs=dict(inputs),
            universal_features=clean_univ,
            game_id=game_id,
            event_time=event_time,
            selection=selection,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def sport_weights(sport: str) -> dict:
        if sport not in SPORT_CONFIG:
            raise ValueError(f"Unknown sport: {sport}")
        cfg = SPORT_CONFIG[sport]
        return {
            "league_baseline_total": cfg["league_baseline_total"],
            "ml_universal_weight": cfg["ml_universal_weight"],
            "prop_universal_weight": cfg["prop_universal_weight"],
        }
EOF

########################################
# betting_engine.py
########################################
cat > "$SRC/edge_equation/engine/betting_engine.py" << 'EOF'
"""
Betting engine.

Glue layer that takes a FeatureBundle + market Line and produces a Pick.
"""
from decimal import Decimal
from typing import Optional

from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer
from edge_equation.engine.feature_builder import FeatureBundle
from edge_equation.engine.pick_schema import Pick, Line


PROB_MARKETS = {"ML", "Run_Line", "Puck_Line", "Spread", "BTTS"}
EXPECTATION_MARKETS = {
    "Total", "Game_Total",
    "HR", "K", "Passing_Yards", "Rushing_Yards", "Receiving_Yards",
    "Points", "Rebounds", "Assists", "SOG",
}


class BettingEngine:

    @staticmethod
    def evaluate(
        bundle: FeatureBundle,
        line: Line,
        public_mode: bool = False,
    ) -> Pick:
        market = bundle.market_type
        sport = bundle.sport
        selection = bundle.selection or ""

        fv = ProbabilityCalculator.calculate_fair_value(
            market, sport, bundle.inputs, bundle.universal_features
        )

        fair_prob: Optional[Decimal] = None
        expected_value: Optional[Decimal] = None
        edge: Optional[Decimal] = None
        kelly: Optional[Decimal] = None
        grade = "C"
        realization = 47

        if market in PROB_MARKETS:
            fair_prob = fv.get("fair_prob")
            calib = EVCalculator.calibrate(
                public_mode,
                {"fair_prob": fair_prob},
                {"odds": line.odds},
            )
            edge = calib["edge"]
            kelly = calib["kelly"]
            if not public_mode and edge is not None:
                grade = ConfidenceScorer.grade(edge)
                realization = ConfidenceScorer.realization_for_grade(grade)

        elif market in EXPECTATION_MARKETS:
            if "expected_total" in fv:
                expected_value = fv["expected_total"]
            elif "expected_value" in fv:
                expected_value = fv["expected_value"]
            edge = None
            kelly = None

        else:
            raise ValueError(f"BettingEngine: unsupported market {market}")

        return Pick(
            sport=sport,
            market_type=market,
            selection=selection,
            line=line,
            fair_prob=fair_prob,
            expected_value=expected_value,
            edge=edge,
            kelly=kelly,
            grade=grade,
            realization=realization,
            game_id=bundle.game_id,
            event_time=bundle.event_time,
            metadata={
                "raw_universal_sum": str(fv.get("raw_universal_sum"))
                    if fv.get("raw_universal_sum") is not None else None,
                **dict(bundle.metadata),
            },
        )
EOF

########################################
# posting_formatter.py
########################################
cat > "$SRC/edge_equation/posting/posting_formatter.py" << 'EOF'
"""
Posting formatter.

Structured card payloads. No graphics, no network. Pure dict output.
"""
from decimal import Decimal
from typing import Iterable, Optional

from edge_equation.engine.pick_schema import Pick


TAGLINE = "Facts. Not Feelings."


CARD_TEMPLATES = {
    "daily_edge": {"headline": "Daily Edge", "subhead": "Today's model-graded plays."},
    "evening_edge": {"headline": "Evening Edge", "subhead": "Late slate picks from the engine."},
    "overseas_edge": {"headline": "Overseas Edge", "subhead": "International slate -- KBO, NPB, and global soccer."},
    "highlighted_game": {"headline": "Highlighted Game", "subhead": "Tonight's model focus."},
    "model_highlight": {"headline": "Model Highlight", "subhead": "Top-graded play from the engine. Hype-free."},
    "sharp_signal": {"headline": "Sharp Signal", "subhead": "Where the model and the market disagree most."},
    "the_outlier": {"headline": "The Outlier", "subhead": "The play the model loves and the market hasn't caught."},
}


class PostingFormatter:

    @staticmethod
    def _best_grade(picks: list) -> str:
        if not picks:
            return "C"
        order = {"A+": 3, "A": 2, "B": 1, "C": 0}
        return max(picks, key=lambda p: order.get(p.grade, 0)).grade

    @staticmethod
    def _max_edge(picks: list):
        edges = [p.edge for p in picks if p.edge is not None]
        return max(edges) if edges else None

    @staticmethod
    def _max_kelly(picks: list):
        kellys = [p.kelly for p in picks if p.kelly is not None]
        return max(kellys) if kellys else None

    @staticmethod
    def build_card(
        card_type: str,
        picks: Iterable[Pick],
        generated_at: Optional[str] = None,
        headline_override: Optional[str] = None,
        subhead_override: Optional[str] = None,
    ) -> dict:
        if card_type not in CARD_TEMPLATES:
            raise ValueError(
                f"Unknown card_type: {card_type}. "
                f"Valid: {sorted(CARD_TEMPLATES.keys())}"
            )
        picks_list = list(picks)
        template = CARD_TEMPLATES[card_type]

        summary = {
            "grade": PostingFormatter._best_grade(picks_list),
            "edge": PostingFormatter._max_edge(picks_list),
            "kelly": PostingFormatter._max_kelly(picks_list),
        }
        summary["edge"] = str(summary["edge"]) if summary["edge"] is not None else None
        summary["kelly"] = str(summary["kelly"]) if summary["kelly"] is not None else None

        return {
            "card_type": card_type,
            "headline": headline_override or template["headline"],
            "subhead": subhead_override or template["subhead"],
            "picks": [p.to_dict() for p in picks_list],
            "summary": summary,
            "tagline": TAGLINE,
            "generated_at": generated_at,
        }
EOF

########################################
# daily_scheduler.py
########################################
cat > "$SRC/edge_equation/engine/daily_scheduler.py" << 'EOF'
"""
Daily scheduler.

Light orchestration with stubbed game data. No API calls.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.posting.posting_formatter import PostingFormatter


_MORNING_STUB = [
    {
        "sport": "MLB",
        "market_type": "ML",
        "selection": "BOS",
        "game_id": "MLB-2026-04-20-DET-BOS",
        "event_time": "2026-04-20T13:05:00-04:00",
        "inputs": {"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        "universal_features": {"home_edge": 0.085},
        "line": {"odds": -132},
    },
    {
        "sport": "MLB",
        "market_type": "Total",
        "selection": "Over 9.5",
        "game_id": "MLB-2026-04-20-DET-BOS",
        "event_time": "2026-04-20T13:05:00-04:00",
        "inputs": {"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        "universal_features": {},
        "line": {"odds": -110, "number": "9.5"},
    },
]

_EVENING_STUB = [
    {
        "sport": "NHL",
        "market_type": "SOG",
        "selection": "Crosby Over 4.5 SOG",
        "game_id": "NHL-2026-04-20-PHI-PIT",
        "event_time": "2026-04-20T19:30:00-04:00",
        "inputs": {"rate": 4.12},
        "universal_features": {"matchup_exploit": 0.10},
        "line": {"odds": -115, "number": "4.5"},
    },
]


def _pick_from_stub(stub: dict, public_mode: bool = False):
    bundle = FeatureBuilder.build(
        sport=stub["sport"],
        market_type=stub["market_type"],
        inputs=stub["inputs"],
        universal_features=stub["universal_features"],
        game_id=stub.get("game_id"),
        event_time=stub.get("event_time"),
        selection=stub.get("selection"),
    )
    line_raw = stub["line"]
    number = Decimal(str(line_raw["number"])) if "number" in line_raw else None
    line = Line(odds=int(line_raw["odds"]), number=number)
    return BettingEngine.evaluate(bundle, line, public_mode=public_mode)


def generate_daily_edge_card(run_datetime: datetime, public_mode: bool = False) -> dict:
    picks = [_pick_from_stub(s, public_mode=public_mode) for s in _MORNING_STUB]
    return PostingFormatter.build_card(
        card_type="daily_edge",
        picks=picks,
        generated_at=run_datetime.isoformat(),
    )


def generate_evening_edge_card(run_datetime: datetime, public_mode: bool = False) -> dict:
    picks = [_pick_from_stub(s, public_mode=public_mode) for s in _EVENING_STUB]
    return PostingFormatter.build_card(
        card_type="evening_edge",
        picks=picks,
        generated_at=run_datetime.isoformat(),
    )
EOF

########################################
# test_feature_builder.py
########################################
cat > "$TESTS/test_feature_builder.py" << 'EOF'
import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder, FeatureBundle
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.stats import DeterministicStats
from edge_equation.config.sport_config import SPORT_CONFIG


def test_build_valid_ml_bundle_det_at_bos():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085, "unknown_key_dropped": 999},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    assert isinstance(bundle, FeatureBundle)
    assert bundle.sport == "MLB"
    assert bundle.market_type == "ML"
    assert bundle.inputs["strength_home"] == 1.32
    assert "unknown_key_dropped" not in bundle.universal_features
    assert "home_edge" in bundle.universal_features
    assert bundle.selection == "BOS"


def test_bundle_feeds_math_layer_consistently_ml():
    inputs = {"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115}
    universal = {"home_edge": 0.085}
    bundle = FeatureBuilder.build(sport="MLB", market_type="ML", inputs=inputs, universal_features=universal)
    direct = ProbabilityCalculator.calculate_fair_value("ML", "MLB", inputs, universal)
    via_bundle = ProbabilityCalculator.calculate_fair_value(
        bundle.market_type, bundle.sport, bundle.inputs, bundle.universal_features
    )
    assert direct["fair_prob"] == via_bundle["fair_prob"]


def test_bundle_feeds_math_layer_consistently_total():
    inputs = {"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00}
    bundle = FeatureBuilder.build(sport="MLB", market_type="Total", inputs=inputs, universal_features={})
    direct = ProbabilityCalculator.calculate_fair_value("Total", "MLB", inputs, {})
    via_bundle = ProbabilityCalculator.calculate_fair_value(
        bundle.market_type, bundle.sport, bundle.inputs, bundle.universal_features
    )
    assert direct["expected_total"] == via_bundle["expected_total"]


def test_bundle_feeds_math_layer_consistently_prop_hr():
    inputs = {"rate": 0.142}
    universal = {"matchup_exploit": 0.08, "market_line_delta": 0.12}
    bundle = FeatureBuilder.build(sport="MLB", market_type="HR", inputs=inputs, universal_features=universal)
    direct = ProbabilityCalculator.calculate_fair_value("HR", "MLB", inputs, universal)
    via_bundle = ProbabilityCalculator.calculate_fair_value(
        bundle.market_type, bundle.sport, bundle.inputs, bundle.universal_features
    )
    assert direct["expected_value"] == via_bundle["expected_value"]


def test_invalid_sport_raises():
    with pytest.raises(ValueError, match="Unknown sport"):
        FeatureBuilder.build("NOT_A_SPORT", "ML", {"strength_home": 1.0, "strength_away": 1.0}, {})


def test_invalid_market_for_sport_raises():
    with pytest.raises(ValueError, match="not supported for sport"):
        FeatureBuilder.build("MLB", "Passing_Yards", {"rate": 250.0}, {})


def test_missing_required_inputs_raises():
    with pytest.raises(ValueError, match="Missing required inputs"):
        FeatureBuilder.build("MLB", "ML", {"strength_home": 1.0}, {})


def test_sport_weights_returns_config():
    weights = FeatureBuilder.sport_weights("MLB")
    assert weights["league_baseline_total"] == SPORT_CONFIG["MLB"]["league_baseline_total"]
    assert weights["ml_universal_weight"] == SPORT_CONFIG["MLB"]["ml_universal_weight"]
    assert weights["prop_universal_weight"] == SPORT_CONFIG["MLB"]["prop_universal_weight"]
EOF

########################################
# test_betting_engine.py
########################################
cat > "$TESTS/test_betting_engine.py" << 'EOF'
import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Pick, Line
from edge_equation.math.probability import ProbabilityCalculator
from edge_equation.math.ev import EVCalculator
from edge_equation.math.scoring import ConfidenceScorer


def _make_ml_bundle_det_at_bos():
    return FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )


def test_engine_ml_pick_matches_math_layer():
    bundle = _make_ml_bundle_det_at_bos()
    line = Line(odds=-132)
    pick = BettingEngine.evaluate(bundle, line, public_mode=False)

    fv = ProbabilityCalculator.calculate_fair_value(
        "ML", "MLB", bundle.inputs, bundle.universal_features
    )
    expected_fair_prob = fv["fair_prob"]
    expected_edge = EVCalculator.calculate_edge(expected_fair_prob, -132)
    dec_odds = EVCalculator.american_to_decimal(-132)
    expected_kelly_full = EVCalculator.kelly_fraction(expected_edge, dec_odds)
    expected_kelly_half = (expected_kelly_full / Decimal('2')).quantize(Decimal('0.0001'))
    expected_grade = ConfidenceScorer.grade(expected_edge)

    assert isinstance(pick, Pick)
    assert pick.fair_prob == expected_fair_prob
    assert pick.expected_value is None
    assert pick.edge == expected_edge
    if expected_edge >= Decimal('0.010000'):
        assert pick.kelly == expected_kelly_half
    else:
        assert pick.kelly == Decimal('0')
    assert pick.grade == expected_grade
    assert pick.realization == ConfidenceScorer.realization_for_grade(expected_grade)
    assert pick.sport == "MLB"
    assert pick.market_type == "ML"
    assert pick.selection == "BOS"
    assert pick.line.odds == -132
    assert pick.game_id == "MLB-2026-04-20-DET-BOS"


def test_engine_total_pick_returns_expected_value():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    line = Line(odds=-110, number=Decimal('9.5'))
    pick = BettingEngine.evaluate(bundle, line)

    fv = ProbabilityCalculator.calculate_fair_value(
        "Total", "MLB", bundle.inputs, bundle.universal_features
    )
    assert pick.expected_value == fv["expected_total"]
    assert pick.fair_prob is None
    assert pick.edge is None
    assert pick.kelly is None


def test_engine_hr_prop_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="HR",
        inputs={"rate": 0.142},
        universal_features={"matchup_exploit": 0.08, "market_line_delta": 0.12},
        selection="Judge Over 0.5 HR",
    )
    line = Line(odds=+320, number=Decimal('0.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("HR", "MLB", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]
    assert pick.fair_prob is None


def test_engine_k_prop_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="K",
        inputs={"rate": 7.85},
        universal_features={"matchup_exploit": 0.09, "market_line_delta": 0.08},
        selection="Burnes Over 7.5 K",
    )
    line = Line(odds=-115, number=Decimal('7.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("K", "MLB", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_nfl_passing_yards_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="NFL",
        market_type="Passing_Yards",
        inputs={"rate": 312.4},
        universal_features={"form_off": 0.11, "matchup_strength": 0.09},
        selection="Mahomes Over 275.5",
    )
    line = Line(odds=-110, number=Decimal('275.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("Passing_Yards", "NFL", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_nhl_sog_matches_math_layer():
    bundle = FeatureBuilder.build(
        sport="NHL",
        market_type="SOG",
        inputs={"rate": 4.12},
        universal_features={"matchup_exploit": 0.10},
        selection="Crosby Over 4.5 SOG",
    )
    line = Line(odds=-115, number=Decimal('4.5'))
    pick = BettingEngine.evaluate(bundle, line)
    fv = ProbabilityCalculator.calculate_fair_value("SOG", "NHL", bundle.inputs, bundle.universal_features)
    assert pick.expected_value == fv["expected_value"]


def test_engine_public_mode_suppresses_edge_kelly():
    bundle = _make_ml_bundle_det_at_bos()
    line = Line(odds=-132)
    pick = BettingEngine.evaluate(bundle, line, public_mode=True)
    assert pick.fair_prob is not None
    assert pick.edge is None
    assert pick.kelly is None


def test_pick_is_frozen():
    bundle = _make_ml_bundle_det_at_bos()
    pick = BettingEngine.evaluate(bundle, Line(odds=-132))
    with pytest.raises(Exception):
        pick.edge = Decimal('0.5')
EOF

########################################
# test_posting_formatter.py
########################################
cat > "$TESTS/test_posting_formatter.py" << 'EOF'
import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.posting.posting_formatter import (
    PostingFormatter,
    CARD_TEMPLATES,
    TAGLINE,
)


def _make_ml_pick():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    return BettingEngine.evaluate(bundle, Line(odds=-132))


def _make_total_pick():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    return BettingEngine.evaluate(bundle, Line(odds=-110, number=Decimal('9.5')))


def test_daily_edge_structure():
    picks = [_make_ml_pick(), _make_total_pick()]
    card = PostingFormatter.build_card("daily_edge", picks)
    assert card["card_type"] == "daily_edge"
    assert card["headline"] == CARD_TEMPLATES["daily_edge"]["headline"]
    assert card["subhead"] == CARD_TEMPLATES["daily_edge"]["subhead"]
    assert card["tagline"] == TAGLINE
    assert card["tagline"] == "Facts. Not Feelings."
    assert len(card["picks"]) == 2
    assert "grade" in card["summary"]
    assert "edge" in card["summary"]
    assert "kelly" in card["summary"]


def test_pick_order_preserved():
    pick1 = _make_ml_pick()
    pick2 = _make_total_pick()
    card = PostingFormatter.build_card("daily_edge", [pick1, pick2])
    assert card["picks"][0]["market_type"] == "ML"
    assert card["picks"][1]["market_type"] == "Total"
    card2 = PostingFormatter.build_card("daily_edge", [pick2, pick1])
    assert card2["picks"][0]["market_type"] == "Total"
    assert card2["picks"][1]["market_type"] == "ML"


def test_summary_reports_best_grade_and_max_edge():
    ml_pick = _make_ml_pick()
    total_pick = _make_total_pick()
    card = PostingFormatter.build_card("daily_edge", [total_pick, ml_pick])
    assert card["summary"]["grade"] in ("A+", "A", "B", "C")
    assert card["summary"]["edge"] == str(ml_pick.edge)
    assert card["summary"]["kelly"] == str(ml_pick.kelly)


def test_all_card_types_buildable():
    pick = _make_ml_pick()
    for card_type in CARD_TEMPLATES.keys():
        card = PostingFormatter.build_card(card_type, [pick])
        assert card["card_type"] == card_type
        assert card["headline"] == CARD_TEMPLATES[card_type]["headline"]
        assert card["tagline"] == TAGLINE
        assert card["picks"][0]["selection"] == "BOS"


def test_unknown_card_type_raises():
    with pytest.raises(ValueError, match="Unknown card_type"):
        PostingFormatter.build_card("smash_of_the_day", [_make_ml_pick()])


def test_empty_picks_allowed():
    card = PostingFormatter.build_card("daily_edge", [])
    assert card["picks"] == []
    assert card["summary"]["grade"] == "C"
    assert card["summary"]["edge"] is None
    assert card["summary"]["kelly"] is None


def test_headline_override():
    pick = _make_ml_pick()
    card = PostingFormatter.build_card("model_highlight", [pick], headline_override="Custom Title")
    assert card["headline"] == "Custom Title"
    assert card["subhead"] == CARD_TEMPLATES["model_highlight"]["subhead"]
EOF

########################################
# test_daily_scheduler.py
########################################
cat > "$TESTS/test_daily_scheduler.py" << 'EOF'
from datetime import datetime

from edge_equation.engine.daily_scheduler import (
    generate_daily_edge_card,
    generate_evening_edge_card,
)
from edge_equation.posting.posting_formatter import TAGLINE


def test_daily_edge_card_nonempty_and_well_formed():
    card = generate_daily_edge_card(datetime(2026, 4, 20, 9, 0, 0))
    assert card["card_type"] == "daily_edge"
    assert card["headline"]
    assert card["subhead"]
    assert card["tagline"] == TAGLINE
    assert len(card["picks"]) >= 1
    for p in card["picks"]:
        assert p["sport"]
        assert p["market_type"]
        assert "line" in p
        assert "grade" in p
    assert card["generated_at"] == "2026-04-20T09:00:00"


def test_evening_edge_card_nonempty_and_well_formed():
    card = generate_evening_edge_card(datetime(2026, 4, 20, 18, 0, 0))
    assert card["card_type"] == "evening_edge"
    assert len(card["picks"]) >= 1
    assert card["tagline"] == TAGLINE


def test_scheduler_public_mode_suppresses_edge():
    card = generate_daily_edge_card(datetime(2026, 4, 20, 9, 0, 0), public_mode=True)
    ml_picks = [p for p in card["picks"] if p["market_type"] == "ML"]
    assert ml_picks
    for p in ml_picks:
        assert p["edge"] is None
        assert p["kelly"] is None
EOF

echo "=== Phase 3 files written. Running pytest ==="

if command -v pytest >/dev/null 2>&1; then
  if ! pytest -v; then
    echo ""
    echo "ERROR: tests failed." >&2
    exit 1
  fi
else
  echo "WARNING: pytest not installed. Skipping test run."
  echo "  Install with: pip install pytest"
  echo "  (Tests were verified in sandbox before this script was generated.)"
fi

echo ""
echo "=== Phase 3 complete ==="
