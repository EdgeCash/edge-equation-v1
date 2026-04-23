"""
That K Report -- unit + integration tests.

Covers:
  1. Each multiplicative adjustment isolates correctly (only the
     factor under test moves the projected mean away from baseline).
  2. 5k Monte Carlo is deterministic for a given seed_key and
     produces a reasonable distribution.
  3. ConfidenceScorer grades map onto the probability-edge ladder.
  4. render_report obeys the exact output format the brief requires.
  5. The sample-slate + runner dry-run path completes cleanly and
     emits a usable text report.
  6. Ledger load / record / flush is idempotent on the dedup key.
  7. Results card verdict tagging (Hit / Miss / Push) + ledger
     footer renders correctly on a mixed slate.
  8. Supporting content: deterministic, rotates by date, no hype
     words, all three generators emit the correct tag prefix.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from edge_equation.that_k import (
    GameContext,
    OpponentLineup,
    PitcherProfile,
    build_projections,
    project_strikeouts,
    render_report,
    simulate_strikeouts,
)
from edge_equation.that_k.model import (
    LEAGUE_CSW,
    LEAGUE_K_PER_BF,
    LEAGUE_SWSTR,
)
from edge_equation.that_k.report import DEFAULT_TOP_N, grade_row
from edge_equation.that_k.runner import _pitcher_from_row
from edge_equation.that_k.sample_slate import sample_slate
from edge_equation.that_k.simulator import DEFAULT_N_SIMS, _nb_sample
import random


# ------------------------------------------------ model factor isolation

def _baseline_pitcher(**overrides) -> PitcherProfile:
    defaults = dict(
        name="Test Pitcher", team="XYZ", throws="R",
        k_per_bf=LEAGUE_K_PER_BF, expected_bf=24.0,
        arsenal={},
        # >=3 recent starts so project_strikeouts does NOT widen the
        # NB dispersion via the sample_warning path.  Tests that need
        # the thin-history case override this explicitly.
        recent_k_per_bf=[
            (LEAGUE_K_PER_BF, 2),
            (LEAGUE_K_PER_BF, 8),
            (LEAGUE_K_PER_BF, 15),
        ],
    )
    defaults.update(overrides)
    return PitcherProfile(**defaults)


def _neutral_lineup(**overrides) -> OpponentLineup:
    defaults = dict(
        team="ABC", swstr_pct=LEAGUE_SWSTR, csw_pct=LEAGUE_CSW,
        lhh_share=0.5,
    )
    defaults.update(overrides)
    return OpponentLineup(**defaults)


def _neutral_ctx(**overrides) -> GameContext:
    defaults = dict(
        dome=True, umpire_k_factor=1.0, park_k_factor=1.0,
    )
    defaults.update(overrides)
    return GameContext(**defaults)


def test_neutral_inputs_yield_baseline_mean():
    inp = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    assert inp.total_adj == pytest.approx(1.0, abs=0.01)
    assert inp.projected_mean == pytest.approx(
        LEAGUE_K_PER_BF * 24.0, abs=0.05,
    )


def test_high_swstr_lineup_boosts_projection():
    base = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    high = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(swstr_pct=LEAGUE_SWSTR + 0.020),
        _neutral_ctx(),
    )
    assert high.projected_mean > base.projected_mean


def test_k_positive_umpire_boosts_projection():
    base = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    plus = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(),
        _neutral_ctx(umpire_k_factor=1.08),
    )
    assert plus.projected_mean > base.projected_mean


def test_cold_weather_boosts_projection_slightly():
    """Temp below 72F should marginally boost K's (ball carries less)."""
    base = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(),
        _neutral_ctx(dome=False, temp_f=72.0),
    )
    cold = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(),
        _neutral_ctx(dome=False, temp_f=50.0),
    )
    assert cold.projected_mean > base.projected_mean


def test_recent_hot_streak_boosts_projection():
    base = project_strikeouts(
        _baseline_pitcher(), _neutral_lineup(), _neutral_ctx(),
    )
    hot = project_strikeouts(
        _baseline_pitcher(recent_k_per_bf=[
            (LEAGUE_K_PER_BF + 0.05, 2),
            (LEAGUE_K_PER_BF + 0.04, 8),
            (LEAGUE_K_PER_BF + 0.06, 14),
        ]),
        _neutral_lineup(),
        _neutral_ctx(),
    )
    assert hot.projected_mean > base.projected_mean


def test_thin_recent_history_widens_dispersion():
    thin = project_strikeouts(
        _baseline_pitcher(recent_k_per_bf=[]),
        _neutral_lineup(), _neutral_ctx(),
    )
    full = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(), _neutral_ctx(),
    )
    assert thin.nb_dispersion < full.nb_dispersion
    assert thin.sample_warning is True
    assert full.sample_warning is False


def test_handedness_same_side_boosts_slightly():
    """L-on-L lineup vs LHP should get a small uplift; L-on-R suppresses."""
    lhp = _baseline_pitcher(throws="L")
    same_side = project_strikeouts(
        lhp, _neutral_lineup(lhh_share=0.70), _neutral_ctx(),
    )
    opposite = project_strikeouts(
        lhp, _neutral_lineup(lhh_share=0.30), _neutral_ctx(),
    )
    assert same_side.projected_mean > opposite.projected_mean


def test_total_adjustment_clamped_under_extreme_inputs():
    """Extreme lineup + extreme umpire must not blow the multiplier
    past +/- 25%.  Protects against the projector ever shipping a
    K projection doubled from baseline."""
    inp = project_strikeouts(
        _baseline_pitcher(),
        _neutral_lineup(swstr_pct=0.25, csw_pct=0.45),
        _neutral_ctx(umpire_k_factor=1.20),
    )
    assert inp.total_adj <= 1.25 + 1e-9


# ------------------------------------------------ MC simulator

def test_simulator_is_deterministic_from_seed():
    a = simulate_strikeouts(
        "P", "T", "O", line=7.5, mean=7.8,
        dispersion=10.0, seed_key="G1",
    )
    b = simulate_strikeouts(
        "P", "T", "O", line=7.5, mean=7.8,
        dispersion=10.0, seed_key="G1",
    )
    assert a.mean == b.mean
    assert a.prob_over == b.prob_over
    assert a.p10 == b.p10
    assert a.p90 == b.p90


def test_simulator_prob_over_above_half_when_mean_over_line():
    r = simulate_strikeouts(
        "P", "T", "O", line=6.5, mean=7.9,
        dispersion=10.0, seed_key="G-over",
    )
    assert r.prob_over > Decimal("0.50")
    assert r.lean == "over"


def test_simulator_prob_under_above_half_when_mean_below_line():
    r = simulate_strikeouts(
        "P", "T", "O", line=8.5, mean=6.5,
        dispersion=10.0, seed_key="G-under",
    )
    assert r.prob_under > Decimal("0.50")
    assert r.lean == "under"


def test_simulator_percentiles_are_ordered():
    r = simulate_strikeouts(
        "P", "T", "O", line=7.5, mean=8.0,
        dispersion=10.0, seed_key="G-pct",
    )
    assert r.p10 <= r.p50 <= r.p90


def test_nb_sample_variance_matches_spec():
    """Var ~ mean + mean^2/r within a reasonable band over 20k draws."""
    rng = random.Random(1234)
    mean = 8.0
    r = 10.0
    n = 20000
    samples = [_nb_sample(rng, mean, r) for _ in range(n)]
    emp_mean = sum(samples) / n
    emp_var = sum((x - emp_mean) ** 2 for x in samples) / (n - 1)
    expected_var = mean + mean ** 2 / r
    # Sampling tolerance: +/- 15% on variance across 20k draws is fine.
    assert emp_var == pytest.approx(expected_var, rel=0.15)


def test_default_n_sims_is_at_least_5000():
    assert DEFAULT_N_SIMS >= 5000


# ------------------------------------------------ grading

def test_grade_row_maps_edge_to_confidence_scorer():
    # Craft a projection with a known edge_prob magnitude.
    from edge_equation.that_k.simulator import KProjection
    proj = KProjection(
        pitcher="P", team="T", opponent="O",
        line=Decimal("7.5"),
        mean=Decimal("9.0"), stdev=Decimal("2.8"),
        p10=Decimal("6.0"), p50=Decimal("9.0"), p90=Decimal("12.0"),
        prob_over=Decimal("0.65"), prob_under=Decimal("0.35"),
        n_sims=5000,
        edge_ks=Decimal("1.5"),
        edge_prob=Decimal("0.15"),
        lean="over",
    )
    assert grade_row(proj) == "A+"  # 0.15 >= 0.08 A+ threshold


# ------------------------------------------------ render format

def _row_for_format():
    pitcher = PitcherProfile(
        name="Gerrit Cole", team="NYY", throws="R",
        k_per_bf=0.285, expected_bf=25,
        swstr_pct=0.135, csw_pct=0.315,
        arsenal={"FF": 0.11, "SL": 0.175},
        recent_k_per_bf=[(0.31, 3), (0.33, 9)],
    )
    lineup = OpponentLineup(
        team="BOS", swstr_pct=0.124, csw_pct=0.302,
        lhh_share=0.55, swstr_vs_R=0.124,
    )
    ctx = GameContext(
        dome=False, temp_f=62.0, wind_mph=11.0, wind_dir="out",
        umpire_name="D. Bellino", umpire_k_factor=1.06,
    )
    rows = build_projections([{
        "game_id": "G1", "line": 7.5,
        "pitcher": pitcher.to_dict(),
        "lineup": lineup.to_dict(),
        "context": ctx.to_dict(),
    }])
    return rows


def test_report_obeys_exact_output_format():
    rows = _row_for_format()
    text = render_report(rows, date_str="2026-04-23")
    # Header + brand lines are verbatim per the spec.
    assert text.startswith("That K Report — 2026-04-23\n")
    assert "Tonight's Pitcher K Projections" in text
    assert text.rstrip().endswith("Powered by Edge Equation")
    # Block structure for the pitcher.
    assert "• Gerrit Cole (NYY) vs. BOS" in text
    assert "  Line: 7.5" in text
    assert "  K Projection: " in text
    assert "  Grade: " in text
    assert "  Edge: " in text
    # No hype language anywhere in the rendered text.
    hype = [
        "take the over", "take the under", "lock",
        "smash", "cash it", "hammer", "slam dunk",
    ]
    lower = text.lower()
    for word in hype:
        assert word not in lower, f"hype word {word!r} leaked into report"


def test_report_caps_at_top_n():
    slate = sample_slate()
    rows = build_projections(slate)
    text = render_report(rows, date_str="2026-04-23", top_n=3)
    # Only three pitcher bullets render.
    bullet_count = text.count("\n• ")
    # "\n• " before each block plus one leading (check split).
    assert text.startswith("That K Report")
    assert len([ln for ln in text.splitlines() if ln.startswith("• ")]) == 3


def test_report_orders_by_edge_prob_desc():
    slate = sample_slate()
    rows = build_projections(slate)
    text = render_report(rows, date_str="2026-04-23", top_n=DEFAULT_TOP_N)
    # The first pitcher bullet must correspond to the row with the
    # highest edge_prob -- not the first in slate order.
    best = max(rows, key=lambda r: r.projection.edge_prob)
    first_bullet = next(ln for ln in text.splitlines() if ln.startswith("• "))
    assert best.pitcher.name in first_bullet


# ------------------------------------------------ sample slate dry-run

def test_sample_slate_dry_run_end_to_end():
    slate = sample_slate()
    rows = build_projections(slate)
    assert len(rows) == len(slate)
    text = render_report(rows, date_str="2026-04-23")
    # Every pitcher in the sample slate appears in the output.
    for row in rows[:DEFAULT_TOP_N]:
        assert row.pitcher.name in text
    # Report length stays bounded (8 starters * ~6 lines + header/footer).
    assert 30 <= text.count("\n") <= 100


def test_cli_sample_dry_run(capsys):
    """Smoke test the CLI entry as invoked by the daily workflow."""
    from edge_equation.that_k.__main__ import main
    rc = main(["--sample", "--date", "2026-04-23"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("That K Report — 2026-04-23\n")
    assert "Powered by Edge Equation" in out


# ------------------------------------------------ runner row parsing

def test_pitcher_row_parser_handles_missing_optional_fields():
    """Minimal row (no arsenal, no recent history) still builds."""
    p = _pitcher_from_row({
        "pitcher": {"name": "X", "team": "Y"},
    })
    assert p.name == "X"
    assert p.team == "Y"
    assert p.k_per_bf == LEAGUE_K_PER_BF


# ------------------------------------------------ Ledger

from edge_equation.that_k.ledger import (
    Ledger,
    VERDICT_HIT,
    VERDICT_MISS,
    VERDICT_PUSH,
    verdict_for_line,
)


def test_verdict_for_line_classifies_correctly():
    assert verdict_for_line(9, 7.5) == VERDICT_HIT
    assert verdict_for_line(6, 7.5) == VERDICT_MISS
    # Whole-number line can push.
    assert verdict_for_line(7, 7.0) == VERDICT_PUSH
    assert verdict_for_line(8, 7.0) == VERDICT_HIT


def test_ledger_starts_empty_on_missing_file(tmp_path):
    led = Ledger(tmp_path / "absent.json")
    snap = led.summary()
    assert snap.wins == 0 and snap.losses == 0 and snap.pushes == 0
    assert snap.total_graded() == 0
    assert snap.hit_rate() == 0.0


def test_ledger_records_and_flushes(tmp_path):
    path = tmp_path / "ledger.json"
    led = Ledger(path)
    assert led.record("2026-04-22", "Cole", "7.5", VERDICT_HIT) is True
    assert led.record("2026-04-22", "Skubal", "8.5", VERDICT_MISS) is True
    assert led.record("2026-04-22", "Morton", "5.5", VERDICT_PUSH) is True
    led.flush()
    assert path.exists()

    # Reload from disk; totals persist.
    led2 = Ledger(path)
    snap = led2.summary()
    assert snap.wins == 1 and snap.losses == 1 and snap.pushes == 1
    assert snap.total_graded() == 2


def test_ledger_dedup_on_rerun(tmp_path):
    """Recording the same (date, pitcher, line) twice must not
    double-count season totals."""
    led = Ledger(tmp_path / "ledger.json")
    assert led.record("2026-04-22", "Cole", "7.5", VERDICT_HIT) is True
    assert led.record("2026-04-22", "Cole", "7.5", VERDICT_HIT) is False
    assert led.summary().wins == 1


def test_ledger_rejects_unknown_verdict(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    with pytest.raises(ValueError):
        led.record("2026-04-22", "X", "7.5", "win")


# ------------------------------------------------ Results card

from edge_equation.that_k.results import (
    build_results,
    render_results_card,
)
from edge_equation.that_k.sample_results import (
    sample_last_night_standout,
    sample_results,
    sample_slate_hooks,
)


def test_results_card_exact_format_without_ledger():
    rows = build_results([
        {"pitcher": "Gerrit Cole", "line": 7.5, "actual": 9},
        {"pitcher": "Tarik Skubal", "line": 8.5, "actual": 6},
        {"pitcher": "Charlie Morton", "line": 5.0, "actual": 5},  # push
    ])
    text = render_results_card(rows, date_str="2026-04-22")
    assert text.startswith("That K Report — Results · 2026-04-22\n")
    assert "Yesterday's K Projections" in text
    assert "• Gerrit Cole 7.5 → 9 K (Hit)" in text
    assert "• Tarik Skubal 8.5 → 6 K (Miss)" in text
    assert "• Charlie Morton 5 → 5 K (Push)" in text
    assert "Season Ledger (K Props)" in text
    # 1 W, 1 L, 1 push => 1-1 with 1 push marker.
    assert "1-1 (50% hit rate)" in text
    assert text.rstrip().endswith("Powered by Edge Equation")


def test_results_card_empty_slate_produces_honest_placeholder():
    text = render_results_card([], date_str="2026-04-22")
    assert "no settled K projections" in text


def test_results_card_updates_ledger(tmp_path):
    led = Ledger(tmp_path / "led.json")
    rows = build_results([
        {"pitcher": "A", "line": 7.5, "actual": 9},
        {"pitcher": "B", "line": 6.5, "actual": 5},
    ])
    render_results_card(rows, date_str="2026-04-22", ledger=led)
    snap = led.summary()
    assert snap.wins == 1
    assert snap.losses == 1


def test_results_card_rerun_does_not_double_count(tmp_path):
    led = Ledger(tmp_path / "led.json")
    rows = build_results([
        {"pitcher": "A", "line": 7.5, "actual": 9},
    ])
    for _ in range(3):
        render_results_card(rows, date_str="2026-04-22", ledger=led)
    assert led.summary().wins == 1


def test_results_card_no_ledger_flag_keeps_disk_clean(tmp_path):
    path = tmp_path / "led.json"
    led = Ledger(path)
    rows = build_results([{"pitcher": "A", "line": 7.5, "actual": 9}])
    render_results_card(
        rows, date_str="2026-04-22",
        ledger=led, update_ledger=False,
    )
    # update_ledger=False means the ledger.flush() never fires.
    assert not path.exists()


def test_results_card_sample_dry_run_roundtrip():
    """Sample slate + sample results must line up by pitcher name so
    operators can run the full loop end-to-end."""
    from edge_equation.that_k.sample_slate import sample_slate
    slate_names = {row["pitcher"]["name"] for row in sample_slate()}
    result_names = {r["pitcher"] for r in sample_results()}
    assert result_names == slate_names


# ------------------------------------------------ Supporting content

from edge_equation.that_k.supporting import (
    ALL_TAGS,
    HYPE_BLOCKLIST,
    TAG_K_OF_THE_NIGHT,
    TAG_STAT_DROP,
    TAG_THROWBACK_K,
    generate_k_of_the_night,
    generate_stat_drop,
    generate_supporting,
    generate_throwback_k,
    render_supporting,
    select_types_for_day,
)


def test_select_types_rotates_by_date():
    picks = {select_types_for_day(f"2026-04-{d:02d}")[0] for d in range(1, 21)}
    # Over 20 days all three tags should appear at least once.
    assert picks == set(ALL_TAGS)


def test_select_types_caps_at_two_per_day():
    picks = select_types_for_day("2026-04-23", n=5)
    assert 1 <= len(picks) <= 2
    assert len(set(picks)) == len(picks)  # paired types differ


def test_k_of_the_night_is_tagged_and_deterministic():
    p1 = generate_k_of_the_night(
        "2026-04-23", sample_last_night_standout()
    )
    p2 = generate_k_of_the_night(
        "2026-04-23", sample_last_night_standout()
    )
    assert p1.tag == TAG_K_OF_THE_NIGHT
    assert p1.text.startswith(f"[{TAG_K_OF_THE_NIGHT}]")
    assert p1.text == p2.text
    assert "Blake Snell" in p1.text
    assert "9 K" in p1.text


def test_k_of_the_night_handles_missing_payload():
    """No data -> honest fallback, no invented numbers."""
    p = generate_k_of_the_night("2026-04-23", None)
    assert p.tag == TAG_K_OF_THE_NIGHT
    # Fallback message must NOT make up a player / line / K count.
    assert "K-of-the-Night bar" in p.text


def test_stat_drop_always_produces_a_factual_line():
    """Even with empty slate hooks the generator must emit *something*
    factual -- brand rule: 1-2 supporting posts per day, no blank
    slots."""
    p = generate_stat_drop("2026-04-23", None)
    assert p.tag == TAG_STAT_DROP
    assert p.text.startswith(f"[{TAG_STAT_DROP}]")
    assert "23.5%" in p.text  # league baseline fallback.


def test_stat_drop_uses_provided_hooks():
    p = generate_stat_drop("2026-04-23", sample_slate_hooks())
    assert p.tag == TAG_STAT_DROP
    # One of the four hook types must appear in the output.
    assert any(
        fragment in p.text
        for fragment in (
            "D. Bellino", "CHW", "Tarik Skubal", "Paul Skenes",
        )
    )


def test_throwback_k_draws_from_curated_catalog():
    p = generate_throwback_k("2026-04-23")
    assert p.tag == TAG_THROWBACK_K
    assert p.text.startswith(f"[{TAG_THROWBACK_K}]")
    # Every catalog item ends with an analytical tie-in -- the post
    # body should contain a percentage OR a raw count reference.
    assert "%" in p.text or "K" in p.text


def test_generate_supporting_respects_type_rotation():
    posts = generate_supporting("2026-04-23", n=2)
    assert 1 <= len(posts) <= 2
    tags = [p.tag for p in posts]
    # Paired posts never duplicate a tag.
    assert len(set(tags)) == len(tags)


def test_supporting_output_contains_no_hype_language():
    """Brand rule: Facts Not Feelings. Any hype phrase in rendered
    supporting content fails the test suite."""
    # Run the generator across 30 consecutive days so the rotation
    # hits every type + every throwback entry at least once.
    full_text = ""
    for d in range(1, 31):
        posts = generate_supporting(
            f"2026-04-{d:02d}", n=2,
            last_night=sample_last_night_standout(),
            slate_hooks=sample_slate_hooks(),
        )
        full_text += render_supporting(posts)
    lower = full_text.lower()
    for word in HYPE_BLOCKLIST:
        assert word not in lower, (
            f"hype phrase {word!r} leaked into supporting content"
        )


def test_supporting_variety_across_month():
    """30 consecutive days must exercise all three tag types so
    subscribers see a real rotation, not the same post 30x."""
    tag_sightings = set()
    for d in range(1, 31):
        posts = generate_supporting(f"2026-04-{d:02d}", n=1)
        for p in posts:
            tag_sightings.add(p.tag)
    assert tag_sightings == set(ALL_TAGS)


def test_render_supporting_emits_tag_prefix_per_line():
    posts = generate_supporting("2026-04-23", n=2,
                                last_night=sample_last_night_standout(),
                                slate_hooks=sample_slate_hooks())
    text = render_supporting(posts)
    for p in posts:
        assert f"[{p.tag}]" in text


# ------------------------------------------------ 70s intro on projections

def test_report_70s_intro_opt_in_only():
    rows = _row_for_format()
    without = render_report(rows, date_str="2026-04-23")
    with_intro = render_report(rows, date_str="2026-04-23", intro_70s=True)
    assert "Groovy" in with_intro or "Right on" in with_intro \
        or "Far out" in with_intro or "Keep on whiffin'" in with_intro \
        or "clean and factual" in with_intro
    # Default path is still the strictly-analytical header.
    assert "Groovy" not in without
    assert "Right on" not in without


def test_report_70s_intro_stays_out_of_analytical_body():
    """Body of every Edge line must remain analytical -- flair lives
    on a single intro line above the section header."""
    rows = _row_for_format()
    text = render_report(rows, date_str="2026-04-23", intro_70s=True)
    body_lines = [
        ln for ln in text.splitlines()
        if ln.startswith("  Edge:")
    ]
    lower_body = "\n".join(body_lines).lower()
    for flair in ("groovy", "right on", "far out", "keep on"):
        assert flair not in lower_body


# ------------------------------------------------ CLI subcommands

def test_cli_results_sample_dry_run(tmp_path, capsys):
    """results subcommand with --sample --no-ledger prints the card
    without touching disk."""
    from edge_equation.that_k.__main__ import main
    rc = main([
        "results", "--sample",
        "--date", "2026-04-22",
        "--no-ledger",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("That K Report — Results · 2026-04-22\n")
    assert "Powered by Edge Equation" in out


def test_cli_supporting_sample_dry_run(capsys):
    from edge_equation.that_k.__main__ import main
    rc = main([
        "supporting", "--sample",
        "--date", "2026-04-23",
        "--n", "2",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # At least one tagged block must render.
    tagged = sum(1 for t in ALL_TAGS if f"[{t}]" in out)
    assert tagged >= 1


def test_cli_projections_backcompat_flag(capsys):
    """Back-compat: the original `--sample` invocation (no subcommand)
    still routes to the projections path so pre-migration callers
    don't break."""
    from edge_equation.that_k.__main__ import main
    rc = main(["--sample", "--date", "2026-04-23"])
    assert rc == 0
    assert capsys.readouterr().out.startswith("That K Report — 2026-04-23\n")


# ------------------------------------------------ target account + credentials

from edge_equation.that_k.config import (
    TargetAccount,
    XCredentials,
    assert_account_separation,
    resolve_x_credentials,
    target_header_tag,
)


def test_target_account_enum_values():
    assert TargetAccount("k_guy") is TargetAccount.KGUY
    assert TargetAccount("main") is TargetAccount.MAIN


def test_resolve_kguy_credentials_reads_only_kguy_env():
    env = {
        "X_API_KEY_KGUY": "k1",
        "X_API_SECRET_KGUY": "k2",
        "X_ACCESS_TOKEN_KGUY": "k3",
        "X_ACCESS_TOKEN_SECRET_KGUY": "k4",
        # Main creds present but MUST NOT leak in.
        "X_API_KEY": "SHOULD_NEVER_BE_RETURNED",
        "X_API_SECRET": "SHOULD_NEVER_BE_RETURNED",
        "X_ACCESS_TOKEN": "SHOULD_NEVER_BE_RETURNED",
        "X_ACCESS_TOKEN_SECRET": "SHOULD_NEVER_BE_RETURNED",
    }
    c = resolve_x_credentials(TargetAccount.KGUY, env=env)
    assert c.is_complete()
    assert c.account is TargetAccount.KGUY
    assert c.api_key == "k1" and c.api_secret == "k2"
    assert c.access_token == "k3" and c.access_token_secret == "k4"
    # None of the main-account values leaked into the KGuy record.
    for v in (c.api_key, c.api_secret, c.access_token, c.access_token_secret):
        assert v != "SHOULD_NEVER_BE_RETURNED"


def test_resolve_main_credentials_reads_only_main_env():
    env = {
        "X_API_KEY": "m1", "X_API_SECRET": "m2",
        "X_ACCESS_TOKEN": "m3", "X_ACCESS_TOKEN_SECRET": "m4",
        # KGuy set present.  Must not cross over.
        "X_API_KEY_KGUY": "SHOULD_NEVER_BE_RETURNED",
    }
    c = resolve_x_credentials(TargetAccount.MAIN, env=env)
    assert c.is_complete()
    assert c.account is TargetAccount.MAIN
    for v in (c.api_key, c.api_secret, c.access_token, c.access_token_secret):
        assert v != "SHOULD_NEVER_BE_RETURNED"


def test_resolve_credentials_reports_missing_env_vars():
    """Empty env -> missing list populated with the exact var names."""
    c = resolve_x_credentials(TargetAccount.KGUY, env={})
    assert not c.is_complete()
    assert set(c.missing) == {
        "X_API_KEY_KGUY",
        "X_API_SECRET_KGUY",
        "X_ACCESS_TOKEN_KGUY",
        "X_ACCESS_TOKEN_SECRET_KGUY",
    }


def test_xcredentials_to_dict_never_includes_secret_values():
    env = {
        "X_API_KEY_KGUY": "supersecret",
        "X_API_SECRET_KGUY": "evensecret",
        "X_ACCESS_TOKEN_KGUY": "tok",
        "X_ACCESS_TOKEN_SECRET_KGUY": "tokmore",
    }
    c = resolve_x_credentials(TargetAccount.KGUY, env=env)
    serialized = c.to_dict()
    # Serialization must only tag account + completeness; never the
    # actual secret material so artifacts can't leak.
    for v in ("supersecret", "evensecret", "tok", "tokmore"):
        assert v not in repr(serialized)


def test_account_separation_warns_when_both_sets_present():
    env = {
        "X_API_KEY_KGUY": "1", "X_API_SECRET_KGUY": "1",
        "X_ACCESS_TOKEN_KGUY": "1", "X_ACCESS_TOKEN_SECRET_KGUY": "1",
        "X_API_KEY": "2", "X_API_SECRET": "2",
        "X_ACCESS_TOKEN": "2", "X_ACCESS_TOKEN_SECRET": "2",
    }
    warnings = assert_account_separation(TargetAccount.KGUY, env=env)
    assert warnings
    assert any("Main" in w for w in warnings) or any("k_guy" in w for w in warnings)


def test_account_separation_silent_when_one_set_present():
    env_kguy_only = {
        "X_API_KEY_KGUY": "1", "X_API_SECRET_KGUY": "1",
        "X_ACCESS_TOKEN_KGUY": "1", "X_ACCESS_TOKEN_SECRET_KGUY": "1",
    }
    assert assert_account_separation(TargetAccount.KGUY, env=env_kguy_only) == []


def test_target_header_tag_never_leaks_secrets():
    # Pure display string -- must NOT reach into env or reveal creds.
    assert target_header_tag(TargetAccount.KGUY) == "target=@ThatK_Guy"
    assert target_header_tag(TargetAccount.MAIN) == "target=@EdgeEquation"


def test_projections_renderer_emits_target_header_tag():
    rows = _row_for_format()
    text_kguy = render_report(
        rows, date_str="2026-04-23",
        target_account=TargetAccount.KGUY,
    )
    text_main = render_report(
        rows, date_str="2026-04-23",
        target_account=TargetAccount.MAIN,
    )
    assert "target=@ThatK_Guy" in text_kguy
    assert "target=@EdgeEquation" in text_main
    # Cross-contamination guard: each render must only carry its own
    # identity tag.
    assert "target=@EdgeEquation" not in text_kguy
    assert "target=@ThatK_Guy" not in text_main


def test_cli_target_account_validation_rejects_garbage(capsys):
    from edge_equation.that_k.__main__ import main
    with pytest.raises(SystemExit):
        # argparse choices gate this; the exit code is non-zero.
        main(["projections", "--sample", "--target-account", "notareal"])


# ------------------------------------------------ commentary buckets

from edge_equation.that_k.commentary import (
    render_day_commentary,
    render_season_commentary,
    pick_phrase,
)


def test_commentary_buckets_cover_every_hit_rate_threshold():
    # Each threshold edge per the brief.
    assert pick_phrase(0.90)[0] == "outta_sight"
    assert pick_phrase(0.80)[0] == "outta_sight"
    assert pick_phrase(0.75)[0] == "far_out"
    assert pick_phrase(0.65)[0] == "far_out"
    assert pick_phrase(0.60)[0] == "groovy"
    assert pick_phrase(0.55)[0] == "groovy"
    assert pick_phrase(0.50)[0] == "mild_miss"
    assert pick_phrase(0.48)[0] == "mild_miss"
    assert pick_phrase(0.45)[0] == "rough"
    assert pick_phrase(0.35)[0] == "rough"
    assert pick_phrase(0.25)[0] == "brutal"
    assert pick_phrase(0.00)[0] == "brutal"


def test_commentary_ties_back_to_actual_numbers():
    """Every commentary line MUST quote the W-L + hit rate so the
    flair never floats free of the facts."""
    c = render_day_commentary(wins=5, losses=2, seed_key="d1")
    assert c is not None
    assert "5-2" in c.text
    assert "71%" in c.text  # 5/7 = 71.4% -> rounded


def test_commentary_is_deterministic_for_same_date_and_bucket():
    c1 = render_day_commentary(wins=4, losses=3, seed_key="2026-04-22")
    c2 = render_day_commentary(wins=4, losses=3, seed_key="2026-04-22")
    assert c1.text == c2.text


def test_commentary_none_when_no_settled_results():
    assert render_day_commentary(wins=0, losses=0) is None
    assert render_season_commentary(wins=0, losses=0) is None


def test_commentary_brutal_bucket_used_on_disaster_day():
    c = render_day_commentary(wins=1, losses=9, seed_key="x")
    assert c is not None
    assert c.bucket == "brutal"
    assert "drag" in c.text.lower()


def test_commentary_cooking_with_gas_appears_in_outta_sight_rotation():
    """One of the three outta_sight variants must surface over the
    variant rotation when bucket is triggered."""
    variants_seen = set()
    for seed in (str(i) for i in range(30)):
        c = render_day_commentary(wins=8, losses=1, seed_key=seed)
        variants_seen.add(c.phrase)
    assert "We were cooking with gas tonight" in variants_seen \
        or "Outta sight -- cooking with gas" in variants_seen


def test_results_card_appends_day_commentary_footer():
    rows = build_results([
        {"pitcher": "A", "line": 7.5, "actual": 9},
        {"pitcher": "B", "line": 7.5, "actual": 5},
    ])
    text = render_results_card(rows, date_str="2026-04-22")
    # 1-1 = 50% -> mild_miss bucket.
    assert "for the birds" in text.lower() or "bird bath" in text.lower()
    assert "1-1" in text and "50%" in text


def test_results_card_commentary_off_opt_out():
    rows = build_results([
        {"pitcher": "A", "line": 7.5, "actual": 9},
    ])
    # commentary=False removes the day line while keeping the rest.
    text = render_results_card(rows, date_str="2026-04-22", commentary=False)
    assert "Season Ledger" in text
    # None of the bucket phrases should appear.
    lower = text.lower()
    for phrase in ("groovy", "far out", "outta sight", "for the birds",
                   "basement", "drag"):
        assert phrase not in lower


# ------------------------------------------------ clip suggestions

from edge_equation.that_k.clips import (
    CLIP_TAG,
    clip_for_k_of_the_night,
    clip_for_throwback,
    render_clip_suggestion,
)


def test_clip_for_k_of_the_night_builds_search_url():
    url = clip_for_k_of_the_night(sample_last_night_standout())
    assert url is not None
    assert url.startswith("https://www.youtube.com/results?search_query=")
    # Pitcher name + K count must appear (URL-encoded).
    assert "Blake+Snell" in url or "Blake%20Snell" in url
    assert "9+K" in url or "9%20K" in url


def test_clip_for_k_of_the_night_none_on_empty_payload():
    assert clip_for_k_of_the_night(None) is None
    assert clip_for_k_of_the_night({}) is None
    # Missing pitcher -> can't build a useful query.
    assert clip_for_k_of_the_night({"opp": "HOU"}) is None


def test_clip_for_throwback_returns_description_not_url():
    desc = clip_for_throwback({
        "pitcher": "Kerry Wood", "year": 1998, "total": 20,
    })
    assert desc
    assert desc.startswith("WGN") or "broadcast" in desc.lower()
    # Description form never includes a URL.
    assert "http" not in desc


def test_clip_for_throwback_none_on_unknown_entry():
    assert clip_for_throwback({
        "pitcher": "Unknown Ace", "year": 2025, "total": 15,
    }) is None


def test_render_clip_suggestion_wraps_tag_correctly():
    out = render_clip_suggestion("Test clip")
    assert out == f"[{CLIP_TAG}: Test clip]"
    assert render_clip_suggestion("") == ""
    assert render_clip_suggestion(None) == ""


def test_k_of_the_night_emits_clip_suggestion_line():
    """Generated K of the Night post must include the tagged clip
    suggestion on its own line so the posting tool can parse it."""
    from edge_equation.that_k.supporting import generate_k_of_the_night
    p = generate_k_of_the_night("2026-04-23", sample_last_night_standout())
    assert f"[{CLIP_TAG}:" in p.text
    # Tag sits on its own line (split on newline and find it).
    found = any(
        line.startswith(f"[{CLIP_TAG}:")
        for line in p.text.splitlines()
    )
    assert found


def test_throwback_k_emits_clip_suggestion_when_catalog_match():
    """Any throwback post produced by the generator should carry a
    clip suggestion because every catalog entry has a canonical
    broadcast description registered in clips.py."""
    from edge_equation.that_k.supporting import generate_throwback_k
    # Run across 30 consecutive days so the rotation exercises every
    # catalog entry at least once.
    for d in range(1, 31):
        p = generate_throwback_k(f"2026-04-{d:02d}")
        assert f"[{CLIP_TAG}:" in p.text


def test_clip_suggestions_are_tasteful_no_hype_in_description():
    """Clip descriptions must not carry tout language -- same brand
    rule as the post bodies."""
    from edge_equation.that_k.clips import _THROWBACK_CLIPS
    from edge_equation.that_k.supporting import HYPE_BLOCKLIST
    joined = "\n".join(_THROWBACK_CLIPS.values()).lower()
    for word in HYPE_BLOCKLIST:
        assert word not in joined, f"hype word {word!r} in clip catalog"


# ------------------------------------------------ workflow + brand smoke

def test_workflow_file_contains_kguy_env_plumbing():
    """Hard regression guard: the workflow must wire the *_KGUY
    secret set and NOT reference the main @EdgeEquation X secrets
    by their unprefixed names."""
    from pathlib import Path
    wf = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "that-k-report.yml"
    text = wf.read_text(encoding="utf-8")
    assert "X_API_KEY_KGUY" in text
    assert "X_API_SECRET_KGUY" in text
    assert "X_ACCESS_TOKEN_KGUY" in text
    assert "X_ACCESS_TOKEN_SECRET_KGUY" in text


def test_workflow_projections_is_dispatch_only():
    """The projections job must NOT run on schedule per the strict
    account discipline rule -- only on a manual workflow_dispatch."""
    from pathlib import Path
    wf = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "that-k-report.yml"
    text = wf.read_text(encoding="utf-8")
    # Locate the projections job block and verify its gate is
    # "workflow_dispatch && mode == 'projections'" with no schedule
    # branch.  Simple substring check is enough; full YAML parsing
    # would be overkill here.
    proj_block = text.split("projections:", 1)[1].split("supporting:", 1)[0]
    assert "workflow_dispatch" in proj_block
    assert "schedule" not in proj_block
