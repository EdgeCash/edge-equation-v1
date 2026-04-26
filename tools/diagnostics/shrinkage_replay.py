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
    """Pull `strength` toward the league-average prior of 1.0 by (1-alpha).
       alpha=1.00 -> no change; alpha=0.50 -> halfway to 1.0; alpha=0 -> 1.0"""
    return (1.0 - alpha) * 1.0 + alpha * strength


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
    print(header("Top-line metrics"))
    for a in alphas:
        edges = [float(replay(p, a).edge) for p in PICKS]
        a_plus = sum(1 for p in PICKS if replay(p, a).grade == "A+")
        print(f"alpha={a:.2f}  "
              f"max_edge={max(edges)*100:>6.2f}%  "
              f"median_edge={sorted(edges)[len(edges) // 2]*100:>6.2f}%  "
              f"A+ count={a_plus:>2} of {len(PICKS)}")


if __name__ == "__main__":
    main()
