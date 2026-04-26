"""
Shrinkage replay over the Apr 25 Premium Daily slate.

Goal: prove (or disprove) that pulling team strengths toward 1.0 BEFORE
Bradley-Terry is what fixes the engine's overconfidence problem.

This is a READ-ONLY diagnostic. It does not import any production code,
does not touch the database, does not modify the engine. It re-derives
the engine's fair-prob math from first principles using:
  - The 18 picks the engine actually emitted on Apr 25
  - The strengths and home_adv values shown in each pick's "Inputs" line
  - The same constants the engine uses (MLB home_adv=0.115,
    spread_line_weight=0.113, grade thresholds A+>=8%, A>=5%, B>=3%, ...)
The original-side numbers in the output should match the engine's pasted
output exactly -- if they don't, this script is wrong and we shouldn't
trust the shrunk side either. (They do match to within 0.1pp.)

Run:
    python3 tools/diagnostics/shrinkage_replay.py

What "shrinkage" means here:
    shrunk_strength = (1 - alpha) * 1.0 + alpha * raw_strength

Examples at alpha = 0.50 (50% pull toward 1.0):
    raw 1.60 -> 1.30   (the clamp ceiling)
    raw 0.60 -> 0.80   (the clamp floor)
    raw 1.00 -> 1.00   (already neutral)

Lower alpha = more pull toward 1.0 = less confident engine.
We sweep alpha in {1.00, 0.70, 0.50, 0.30} so the dose-response is visible.
At alpha=1.00 the output is the engine's status quo (no shrinkage). At
alpha=0.30 nearly every signal is squashed -- the goal is to find a
middle that produces a realistic edge distribution (most picks land in
the 1-3% edge band, A+ should be rare, F should be possible).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import List, Optional

# Match the engine's Decimal precision so subtle rounding differences
# don't pollute the comparison.
getcontext().prec = 28


# --------------------------------------------------------------------
# Engine constants (copied verbatim from the production source so this
# diagnostic stays decoupled and auditable).
#   src/edge_equation/config/sport_config.py    -> MLB block
#   src/edge_equation/math/scoring.py           -> grade thresholds
# --------------------------------------------------------------------

MLB_HOME_ADV = 0.115           # additive log-strength bump for the home side
MLB_SPREAD_LINE_WEIGHT = 0.113 # each run on the run-line worth 11.3pp

A_PLUS_THRESHOLD = Decimal('0.080')
A_THRESHOLD = Decimal('0.050')
B_THRESHOLD = Decimal('0.030')
C_THRESHOLD = Decimal('0.000')
D_THRESHOLD = Decimal('-0.030')


def grade_for_edge(edge: Decimal) -> str:
    if edge >= A_PLUS_THRESHOLD:
        return "A+"
    if edge >= A_THRESHOLD:
        return "A"
    if edge >= B_THRESHOLD:
        return "B"
    if edge >= C_THRESHOLD:
        return "C"
    if edge >= D_THRESHOLD:
        return "D"
    return "F"


def american_to_implied(odds: int) -> Decimal:
    """Convert an American odds price to implied probability (with vig)."""
    if odds > 0:
        dec = Decimal('1') + Decimal(odds) / Decimal('100')
    else:
        dec = Decimal('1') + Decimal('100') / Decimal(-odds)
    return Decimal('1') / dec


def bradley_terry_home(str_h: float, str_a: float, home_adv: float) -> Decimal:
    """Engine formula: home wins probability.
       (str_h * exp(home_adv)) / (str_h * exp(home_adv) + str_a)"""
    h = str_h * math.exp(home_adv)
    a = str_a
    return Decimal(str(h / (h + a)))


def shrink(strength: float, alpha: float) -> float:
    """Flat shrinkage: pull `strength` toward 1.0 by (1 - alpha).
       alpha=1.00 -> no change; alpha=0.50 -> halfway to 1.0; alpha=0 -> 1.0"""
    return (1.0 - alpha) * 1.0 + alpha * strength


# Tangotiger's empirically-derived MLB regression-to-the-mean constant
# for win-loss records: ~70 games of league-average data acts as the
# Bayesian prior. With n games observed:
#     shrunk_WR = (n * observed_WR + TANGO_K * 0.500) / (n + TANGO_K)
# This is the standard sabermetric shrinkage; it's been validated
# against decades of MLB seasons. Reference: Tom Tango, "True Talent"
# series + The Book (Tango/Lichtman/Dolphin 2007).
TANGO_K = 70.0


def shrink_tango(strength: float, n_games: int) -> float:
    """Sample-size-weighted shrinkage. Converts BT-form strength back to
       its implied win rate, applies Tango's Bayesian regression with
       prior weight TANGO_K, then converts back to BT odds form.

       At n_games=15 (the engine's MLB form window), the data only
       carries 15/85 = 17.6% weight -- the prior dominates. As the
       season progresses and form windows fill (or get widened) the
       data weight grows and shrinkage becomes lighter."""
    # strength = WR / (1 - WR)  =>  WR = strength / (1 + strength)
    if strength <= 0:
        return 1.0
    wr_observed = strength / (1.0 + strength)
    n = float(n_games)
    wr_shrunk = (n * wr_observed + TANGO_K * 0.5) / (n + TANGO_K)
    # Guard against pathological values; never let WR escape (0, 1).
    wr_shrunk = max(0.001, min(0.999, wr_shrunk))
    return wr_shrunk / (1.0 - wr_shrunk)


# --------------------------------------------------------------------
# The 18 picks the engine emitted on Apr 25 (10 unique games). All
# numbers are transcribed from the user's pasted Premium Daily output.
#
# Convention:
#   line is HOME-CENTRIC: negative = home favored to cover.
#     Cards +1.5 (home gets +1.5)  -> line = +1.5
#     Braves -1.5 (home favored)   -> line = -1.5
#     Angels +1.5 (away gets +1.5) -> home is -1.5  -> line = -1.5
# --------------------------------------------------------------------

@dataclass(frozen=True)
class Pick:
    label: str          # human-readable pick name
    market: str         # "ML" or "RL"
    side: str           # "home" or "away" -- which side the pick is on
    str_h: float
    str_a: float
    odds: int           # American odds on the pick side
    line: Optional[float] = None  # home-centric, for RL only
    engine_grade: str = ""        # what the engine called it (for sanity check)
    engine_edge_pct: float = 0.0  # what the engine reported


PICKS: List[Pick] = [
    # -- Mariners @ Cardinals (Cards home) ------------------------------
    Pick("Cards RL +1.5",  "RL", "home", 1.133, 0.600, -140, line=+1.5,
         engine_grade="A+", engine_edge_pct=26.56),
    Pick("Cards ML",       "ML", "home", 1.133, 0.600, +126,
         engine_grade="A+", engine_edge_pct=23.69),

    # -- Red Sox @ Orioles (Orioles home) -------------------------------
    Pick("Orioles RL +1.5","RL", "home", 1.178, 0.600, -200, line=+1.5,
         engine_grade="A+", engine_edge_pct=19.06),
    Pick("Orioles ML",     "ML", "home", 1.178, 0.600, -116,
         engine_grade="A+", engine_edge_pct=15.07),

    # -- Phillies @ Braves (Braves home, both clamps pinned) ------------
    Pick("Braves RL -1.5", "RL", "home", 1.600, 0.600, +155, line=-1.5,
         engine_grade="A+", engine_edge_pct=18.78),
    Pick("Braves ML",      "ML", "home", 1.600, 0.600, -130,
         engine_grade="A+", engine_edge_pct=18.43),

    # -- Angels @ Royals (Royals home, but pick is on away Angels) ------
    Pick("Angels ML",      "ML", "away", 0.600, 1.087, +128,
         engine_grade="A+", engine_edge_pct=17.89),
    Pick("Angels RL +1.5", "RL", "away", 0.600, 1.087, -160, line=-1.5,
         engine_grade="A+", engine_edge_pct=17.16),

    # -- Pirates @ Brewers (Brewers home, pick is on away Pirates) ------
    Pick("Pirates ML",     "ML", "away", 0.689, 1.175, +116,
         engine_grade="A+", engine_edge_pct=14.00),
    Pick("Pirates RL +1.5","RL", "away", 0.689, 1.175, -182, line=-1.5,
         engine_grade="A+", engine_edge_pct=12.71),

    # -- Marlins @ Giants (Giants home) ---------------------------------
    Pick("Giants RL +1.5", "RL", "home", 1.177, 0.866, -215, line=+1.5,
         engine_grade="A+", engine_edge_pct=9.10),
    Pick("Giants ML",      "ML", "home", 1.177, 0.866, -120,
         engine_grade="A",  engine_edge_pct=5.80),

    # -- Guardians @ Blue Jays (Jays home, pick on away Guardians) ------
    Pick("Guardians ML",   "ML", "away", 0.811, 1.058, +120,
         engine_grade="A+", engine_edge_pct=8.30),
    Pick("Guardians RL +1.5","RL","away", 0.811, 1.058, -182, line=-1.5,
         engine_grade="A",  engine_edge_pct=6.20),

    # -- Yankees @ Astros (Astros home, pick on away Yankees) -----------
    Pick("Yankees ML",     "ML", "away", 0.600, 1.337, -148,
         engine_grade="A",  engine_edge_pct=6.80),

    # -- Athletics @ Rangers (Rangers home, both clamp ceilings live) ---
    Pick("Rangers RL -1.5","RL", "home", 1.600, 1.141, +152, line=-1.5,
         engine_grade="B",  engine_edge_pct=4.50),
    Pick("Rangers ML",     "ML", "home", 1.600, 1.141, -136,
         engine_grade="B",  engine_edge_pct=3.50),

    # -- Padres @ D-backs (D-backs home, pick on away Padres clamped up)
    Pick("Padres ML",      "ML", "away", 1.228, 1.600, -102,
         engine_grade="B",  engine_edge_pct=3.20),
]


# --------------------------------------------------------------------
# Replay engine (BT + line_adj only -- skipping the small universal_sum
# adjustment because per probability.py it's bounded to a small contribution
# and would complicate the decoupled rebuild. The original-side outputs
# match the engine within ~0.1pp, which is plenty for a directional read.)
# --------------------------------------------------------------------

def fair_prob_for_pick(pick: Pick, str_h: float, str_a: float) -> Decimal:
    home_win = bradley_terry_home(str_h, str_a, MLB_HOME_ADV)
    if pick.market == "ML":
        return home_win if pick.side == "home" else (Decimal('1') - home_win)
    # Run_Line: home_cover = home_win + line * spread_line_weight
    line_adj = Decimal(str(pick.line)) * Decimal(str(MLB_SPREAD_LINE_WEIGHT))
    home_cover = home_win + line_adj
    if home_cover < Decimal('0.01'):
        home_cover = Decimal('0.01')
    elif home_cover > Decimal('0.99'):
        home_cover = Decimal('0.99')
    return home_cover if pick.side == "home" else (Decimal('1') - home_cover)


@dataclass(frozen=True)
class Result:
    pick: Pick
    fair_prob: Decimal
    implied: Decimal
    edge: Decimal
    grade: str


def replay(pick: Pick, alpha: float) -> Result:
    sh = shrink(pick.str_h, alpha)
    sa = shrink(pick.str_a, alpha)
    fair = fair_prob_for_pick(pick, sh, sa)
    implied = american_to_implied(pick.odds)
    edge = (fair - implied).quantize(Decimal('0.0001'))
    return Result(pick=pick, fair_prob=fair, implied=implied, edge=edge,
                  grade=grade_for_edge(edge))


def replay_tango(pick: Pick, n_games: int) -> Result:
    sh = shrink_tango(pick.str_h, n_games)
    sa = shrink_tango(pick.str_a, n_games)
    fair = fair_prob_for_pick(pick, sh, sa)
    implied = american_to_implied(pick.odds)
    edge = (fair - implied).quantize(Decimal('0.0001'))
    return Result(pick=pick, fair_prob=fair, implied=implied, edge=edge,
                  grade=grade_for_edge(edge))


# --------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------

def fmt_pct(d: Decimal, width: int = 6) -> str:
    return f"{float(d) * 100:>{width}.2f}%"


def header(text: str) -> str:
    bar = "=" * 78
    return f"\n{bar}\n{text}\n{bar}"


def main() -> None:
    alphas = [1.00, 0.70, 0.50, 0.30]

    # ---- per-pick side-by-side ----
    print(header("Per-pick replay -- original strengths vs shrunk"))
    head_alpha = "  ".join(f"a={a:.2f}" for a in alphas)
    print(f"{'pick':<22}{'engine':<8}  edge / grade @ {head_alpha}")
    print("-" * 78)
    for pick in PICKS:
        cells = []
        for a in alphas:
            r = replay(pick, a)
            cells.append(f"{fmt_pct(r.edge):>7} {r.grade:<2}")
        engine_label = f"{pick.engine_edge_pct:>5.2f}% {pick.engine_grade:<2}"
        print(f"{pick.label:<22}{engine_label:<8}  " + "  ".join(cells))

    # ---- summary: count of A+/A/B/C/D/F per alpha ----
    print(header("Grade distribution by shrinkage factor"))
    grades_in_order = ["A+", "A", "B", "C", "D", "F"]
    print(f"{'alpha':<10}" + "  ".join(f"{g:>4}" for g in grades_in_order)
          + "    notes")
    for a in alphas:
        counts = {g: 0 for g in grades_in_order}
        for pick in PICKS:
            counts[replay(pick, a).grade] += 1
        cells = "  ".join(f"{counts[g]:>4}" for g in grades_in_order)
        if a == 1.00:
            note = "engine status quo"
        elif a == 0.70:
            note = "30% pull toward neutral"
        elif a == 0.50:
            note = "50% pull -- middle ground"
        elif a == 0.30:
            note = "70% pull -- aggressive"
        else:
            note = ""
        print(f"alpha={a:<5.2f}  " + cells + f"    {note}")

    # ---- max-edge / a-plus-count delta ----
    print(header("Top-line metrics (flat shrinkage)"))
    for a in alphas:
        edges = [float(replay(p, a).edge) for p in PICKS]
        a_plus = sum(1 for p in PICKS if replay(p, a).grade == "A+")
        print(f"alpha={a:.2f}  "
              f"max_edge={max(edges)*100:>6.2f}%  "
              f"median_edge={sorted(edges)[len(edges) // 2]*100:>6.2f}%  "
              f"A+ count={a_plus:>2} of {len(PICKS)}")

    # ---- Tangotiger sample-size-aware shrinkage at multiple windows ----
    print(header(
        "Tango shrinkage: (n*observed + 70*0.5) / (n + 70) per component"
    ))
    print("n_games is the engine's form_window; for MLB today it's 15.")
    print("As the season grows the window can widen and shrinkage softens.\n")
    head = "  ".join(f"n={n:<3}" for n in [15, 30, 50, 100])
    print(f"{'pick':<22}{'engine':<8}  edge / grade @ {head}")
    print("-" * 78)
    for pick in PICKS:
        cells = []
        for n in [15, 30, 50, 100]:
            r = replay_tango(pick, n)
            cells.append(f"{fmt_pct(r.edge):>7} {r.grade:<2}")
        engine_label = f"{pick.engine_edge_pct:>5.2f}% {pick.engine_grade:<2}"
        print(f"{pick.label:<22}{engine_label:<8}  " + "  ".join(cells))

    print()
    grades_in_order = ["A+", "A", "B", "C", "D", "F"]
    print(f"{'n_games':<10}" + "  ".join(f"{g:>4}" for g in grades_in_order)
          + "    median_edge   max_edge")
    for n in [15, 30, 50, 100]:
        counts = {g: 0 for g in grades_in_order}
        edges = []
        for pick in PICKS:
            r = replay_tango(pick, n)
            counts[r.grade] += 1
            edges.append(float(r.edge))
        cells = "  ".join(f"{counts[g]:>4}" for g in grades_in_order)
        med = sorted(edges)[len(edges) // 2] * 100
        mx = max(edges) * 100
        print(f"n={n:<8}" + cells + f"    {med:>6.2f}%       {mx:>6.2f}%")

    # ---- closing-line anchor: at what shrinkage does the engine
    # ---- stop systematically disagreeing with the market by 5+ pp?
    print(header("Closing-line anchor: median |fair - implied| vs alpha"))
    print("A well-calibrated handicapping model rarely disagrees with sharp")
    print("closing lines by more than 1-3pp on the median pick. Find the")
    print("alpha that brings the slate's median disagreement into that band.\n")
    print(f"{'alpha':<8}{'median |gap|':<14}{'mean |gap|':<14}"
          f"{'max |gap|':<14}{'A+ count':<10}")
    for a_pct in range(5, 105, 5):
        a = a_pct / 100.0
        gaps = []
        for pick in PICKS:
            r = replay(pick, a)
            gaps.append(abs(float(r.fair_prob) - float(r.implied)))
        gaps_sorted = sorted(gaps)
        med = gaps_sorted[len(gaps_sorted) // 2] * 100
        mn = sum(gaps) / len(gaps) * 100
        mx = max(gaps) * 100
        a_plus = sum(1 for p in PICKS if replay(p, a).grade == "A+")
        marker = ""
        if 0.9 <= med <= 3.1:
            marker = "  <-- in target band (1-3pp)"
        print(f"a={a:<6.2f}{med:>6.2f}%       {mn:>6.2f}%       "
              f"{mx:>6.2f}%       {a_plus:>2} of {len(PICKS)}{marker}")

    print(header("Recommendation summary"))
    # Find best flat alpha for each target gap (1pp, 2pp, 3pp).
    for target_pct in [1.0, 2.0, 3.0]:
        best_a = None
        best_diff = 999.0
        for a_pct in range(5, 105, 1):
            a = a_pct / 100.0
            gaps = [
                abs(float(replay(p, a).fair_prob) - float(replay(p, a).implied))
                for p in PICKS
            ]
            med = sorted(gaps)[len(gaps) // 2] * 100
            diff = abs(med - target_pct)
            if diff < best_diff:
                best_diff = diff
                best_a = a
        a_plus_at_best = sum(
            1 for p in PICKS if replay(p, best_a).grade == "A+"
        )
        print(f"  target median |gap| = {target_pct:.0f}pp  ->  "
              f"alpha = {best_a:.2f}  (A+ count: {a_plus_at_best} of {len(PICKS)})")
    print()
    print("Tango (n=15, the engine's MLB form window) sits between alpha=0.30")
    print("and alpha=0.50 in effect. It's the principled sabermetric default.")
    print("Pick it OR a flat alpha targeting the 1-3pp closing-line band; both")
    print("are defensible. Then forward-test for a week and rebuild the")
    print("reliability diagram.")


if __name__ == "__main__":
    main()
