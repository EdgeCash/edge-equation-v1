"""
Phase 26 premium receipts:

  1. Grade Track Record block — per-(sport, grade) historical hit
     rate pulled from settled picks, rendered above the Daily Edge
     section.

  2. Per-pick feature inputs — every pick's bundle.inputs +
     universal_features get stashed into pick.metadata["feature_inputs"],
     then surfaced as an "Inputs:" line inside the premium deep block.
     The PublicModeSanitizer strips the stash from free-content picks.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from edge_equation.compliance.sanitizer import PublicModeSanitizer
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.pick_schema import Line, Pick
from edge_equation.persistence.db import Database
from edge_equation.persistence.pick_store import PickStore
from edge_equation.persistence.slate_store import SlateRecord, SlateStore
from edge_equation.posting.grade_track_record import (
    GradeRecord,
    compute_track_record,
    format_track_record,
)
from edge_equation.posting.posting_formatter import PostingFormatter
from edge_equation.posting.premium_daily_body import (
    _render_feature_inputs,
    _render_pick_block,
    format_premium_daily,
)


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ------------------------------------------------ Grade Track Record


_SEEDED_SLATES: set = set()


def _ensure_slate(conn, slate_id: str) -> None:
    """Insert a slate row the first time we use it so picks.slate_id FK
    is satisfied. Idempotent across multiple calls within a test."""
    if slate_id in _SEEDED_SLATES:
        return
    SlateStore.insert(conn, SlateRecord(
        slate_id=slate_id, generated_at="2026-04-20T12:00:00",
        sport=None, card_type="daily_edge", metadata={},
    ))
    _SEEDED_SLATES.add(slate_id)


def _seed_settled(conn, sport, grade, wins=0, losses=0, pushes=0):
    """Insert settled picks via PickStore (FK-safe) then flip the
    realization column on each row."""
    slate_id = f"seed-{sport}-{grade}"
    _SEEDED_SLATES.discard(slate_id)
    _ensure_slate(conn, slate_id)
    picks = []
    realizations = [100] * wins + [0] * losses + [50] * pushes
    for i in range(len(realizations)):
        picks.append(Pick(
            sport=sport, market_type="ML", selection=f"X-{i}",
            line=Line(odds=-110),
            fair_prob=Decimal("0.55"),
            edge=Decimal("0.05"),
            kelly=Decimal("0.02"),
            grade=grade,
            game_id=f"G-{slate_id}-{i}",
        ))
    if picks:
        PickStore.insert_many(
            conn, picks, slate_id=slate_id,
            recorded_at="2026-04-20T12:00:00",
        )
    # Flip realizations in bulk.
    rows = conn.execute(
        "SELECT id FROM picks WHERE slate_id = ? ORDER BY id",
        (slate_id,),
    ).fetchall()
    for row, rz in zip(rows, realizations):
        conn.execute(
            "UPDATE picks SET realization = ? WHERE id = ?",
            (rz, row["id"]),
        )
    conn.commit()


def test_compute_track_record_aggregates_per_sport_per_grade(conn):
    _seed_settled(conn, "MLB", "A+", wins=47, losses=19, pushes=2)
    _seed_settled(conn, "MLB", "A",  wins=31, losses=24)
    _seed_settled(conn, "NFL", "A",  wins=12, losses=6, pushes=1)

    recs = compute_track_record(conn)
    by_key = {(r.sport, r.grade): r for r in recs}
    assert by_key[("MLB", "A+")].wins == 47
    assert by_key[("MLB", "A+")].losses == 19
    assert by_key[("MLB", "A+")].pushes == 2
    # Hit rate excludes pushes: 47 / (47 + 19) = 0.7121
    assert by_key[("MLB", "A+")].hit_rate == Decimal("0.7121")


def test_compute_track_record_filters_by_sport(conn):
    _seed_settled(conn, "MLB", "A+", wins=5, losses=2)
    _seed_settled(conn, "NFL", "A+", wins=4, losses=1)
    mlb_only = compute_track_record(conn, sports=["MLB"])
    assert {r.sport for r in mlb_only} == {"MLB"}


def test_compute_track_record_min_n_drops_thin_buckets(conn):
    _seed_settled(conn, "MLB", "A+", wins=1, losses=0)   # n_settled = 1
    recs_all = compute_track_record(conn, min_n=1)
    recs_strict = compute_track_record(conn, min_n=5)
    assert recs_all
    assert recs_strict == []


def test_compute_track_record_excludes_voids_and_pendings(conn):
    """Realization -1 (void) and 47 (pending) must not count."""
    slate_id = "seed-void-MLB"
    _ensure_slate(conn, slate_id)
    PickStore.insert_many(
        conn,
        [
            Pick(sport="MLB", market_type="ML", selection="V1",
                 line=Line(odds=-110), fair_prob=Decimal("0.55"),
                 grade="A+", game_id="G-void"),
            Pick(sport="MLB", market_type="ML", selection="V2",
                 line=Line(odds=-110), fair_prob=Decimal("0.55"),
                 grade="A+", game_id="G-pending"),
        ],
        slate_id=slate_id, recorded_at="2026-04-20T12:00:00",
    )
    # Force realizations to void / pending.
    conn.execute("UPDATE picks SET realization = -1 WHERE selection = 'V1'")
    conn.execute("UPDATE picks SET realization = 47 WHERE selection = 'V2'")
    conn.commit()
    assert compute_track_record(conn) == []


def test_format_track_record_is_empty_on_no_records():
    assert format_track_record([]) == ""


def test_format_track_record_shape():
    recs = [
        GradeRecord(sport="MLB", grade="A+", wins=47, losses=19, pushes=2, n_settled=68),
        GradeRecord(sport="MLB", grade="A",  wins=31, losses=24, pushes=0, n_settled=55),
        GradeRecord(sport="NFL", grade="A",  wins=12, losses=6,  pushes=1, n_settled=19),
    ]
    text = format_track_record(recs)
    assert "=== GRADE TRACK RECORD ===" in text
    assert "MLB" in text
    assert "NFL" in text
    # Each engine grade renders as itself; no more "B" -> "A-" relabel.
    assert "A+ 47-19-2" in text
    assert "71.2%" in text
    assert "n=68" in text


def test_format_track_record_renders_engine_b_as_b():
    recs = [
        GradeRecord(sport="MLB", grade="B", wins=4, losses=3, pushes=0, n_settled=7),
    ]
    text = format_track_record(recs)
    # Apr 26: the prior brand "B" -> "A-" relabel made B-tier picks
    # appear as a separate (and nonexistent) "A-" tier in the email.
    # Engine grades are now rendered verbatim throughout premium copy.
    assert "B 4-3-0" in text
    assert "A-" not in text


# ------------------------------------------------ feature_inputs stash


def test_betting_engine_stashes_feature_inputs_on_picks():
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="G-1", selection="NYY",
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-110))
    fi = pick.metadata.get("feature_inputs")
    assert isinstance(fi, dict)
    assert fi["strength_home"] == "1.32"
    assert fi["strength_away"] == "1.15"
    assert fi["home_adv"] == "0.115"
    assert fi["home_edge"] == "0.085"


def test_public_mode_sanitizer_strips_feature_inputs():
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="G-1", selection="NYY",
    )
    pick = BettingEngine.evaluate(bundle, Line(odds=-110))
    sanitized = PublicModeSanitizer.sanitize_pick(pick.to_dict())
    meta = sanitized.get("metadata", {})
    assert "feature_inputs" not in meta
    assert "raw_universal_sum" not in meta


def test_render_feature_inputs_compact_one_line():
    meta = {
        "feature_inputs": {
            "strength_home": "1.32",
            "strength_away": "1.15",
            "home_adv": "0.115",
            "home_edge": "0.085",
        },
    }
    rendered = _render_feature_inputs(meta)
    assert rendered is not None
    assert rendered.startswith("Inputs:")
    # Ordered: strength first, then home_adv, then home_edge.
    assert "str(H) 1.320" in rendered
    assert "str(A) 1.150" in rendered
    assert "home_adv 0.115" in rendered
    assert "home_edge 0.085" in rendered


def test_render_feature_inputs_none_when_empty():
    assert _render_feature_inputs(None) is None
    assert _render_feature_inputs({}) is None
    assert _render_feature_inputs({"feature_inputs": {}}) is None


def test_premium_pick_block_shows_inputs_row():
    p = Pick(
        sport="MLB", market_type="ML", selection="NYY",
        line=Line(odds=-115),
        fair_prob=Decimal("0.62"),
        edge=Decimal("0.09"),
        kelly=Decimal("0.04"),
        grade="A+", game_id="G-1",
        metadata={
            "home_team": "NYY", "away_team": "BOS",
            "feature_inputs": {
                "strength_home": "1.32", "strength_away": "1.15",
                "home_adv": "0.115",
            },
        },
    )
    block = "\n".join(_render_pick_block(p.to_dict()))
    assert "Inputs:" in block
    assert "str(H) 1.320" in block
    assert "home_adv 0.115" in block


# ------------------------------------------------ end-to-end premium render


def test_premium_email_renders_track_record_and_inputs():
    pick = Pick(
        sport="MLB", market_type="ML", selection="NYY",
        line=Line(odds=-115),
        fair_prob=Decimal("0.62"),
        edge=Decimal("0.09"),
        kelly=Decimal("0.04"),
        grade="A+", game_id="G-1",
        metadata={
            "home_team": "NYY", "away_team": "BOS",
            "read_notes": "Pitching matchup favors home side.",
            "feature_inputs": {
                "strength_home": "1.32", "strength_away": "1.15",
                "home_adv": "0.115",
            },
        },
    )
    track_text = format_track_record([
        GradeRecord(sport="MLB", grade="A+", wins=47, losses=19,
                    pushes=2, n_settled=68),
    ])
    card = PostingFormatter.build_card(
        card_type="premium_daily",
        picks=[pick],
        generated_at="2026-04-22T10:00:00",
        grade_track_record_text=track_text,
    )
    body = format_premium_daily(card)
    # Track record section lands before the DAILY EDGE section header.
    assert "GRADE TRACK RECORD" in body
    assert "A+ 47-19-2" in body
    assert body.index("GRADE TRACK RECORD") < body.index("=== DAILY EDGE")
    # Inputs row surfaces inside the pick block.
    assert "Inputs: str(H) 1.320" in body
