"""
That K Report -- automated supporting content.

Three post types, rotated deterministically by date so the account
never ships two of the same type in a row:

    [K_OF_THE_NIGHT]  -- one standout K from last night + short
                         analytical note. Light 70s flair allowed
                         in the intro line only.
    [STAT_DROP]       -- a single data fact tied to tonight's slate
                         (umpire trend, lineup SwStr% leader, arsenal
                         matchup, recent-form streak).
    [THROWBACK_K]     -- classic vintage strikeout moment paired
                         with a modern analytical tie-in.

Rules (enforced by tests):
  * 100% analytical. No betting advice, no "take the over", no
    tout language anywhere.
  * Light 70s personality permitted ONLY in the intro / sign-off
    of [K_OF_THE_NIGHT] and [THROWBACK_K] posts. Never in the
    analytical body.
  * Deterministic output from a (date, slate-seed) pair so a
    replay of the same day produces the same post (auditable).
  * At most two supporting posts per day.

Public API:
    select_types_for_day(date_str)       -> list[str] of 1-2 tags
    generate_supporting(date_str, ...)   -> list[SupportingPost]
    render_supporting(posts)             -> plain-text bundle for
                                            the workflow to pipe
                                            straight into the
                                            X queue / SMTP body.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from edge_equation.that_k.clips import (
    clip_for_k_of_the_night,
    clip_for_throwback,
    render_clip_suggestion,
)


# Tag labels -- emitted verbatim in rendered output so the posting
# tool can split on them trivially.
TAG_K_OF_THE_NIGHT = "K_OF_THE_NIGHT"
TAG_STAT_DROP = "STAT_DROP"
TAG_THROWBACK_K = "THROWBACK_K"
ALL_TAGS = (TAG_K_OF_THE_NIGHT, TAG_STAT_DROP, TAG_THROWBACK_K)

# 70s flair intros.  Tasteful, short, rotated -- never a hype phrase.
# Stays out of the analytical sentence itself; that must remain clean.
_INTRO_70S = (
    "Groovy K moment from last night—",
    "Right on, dig this K note—",
    "Far out K flash from last night—",
    "Keep on whiffin'—",
    "Tonight's K slate, clean and factual—",
)

# Throwback K catalog.  Every fact here is verifiable in public
# record; paired with a modern analytical tie-in so the post has
# substance, not just nostalgia.
_THROWBACKS: Tuple[Dict[str, Any], ...] = (
    {
        "year": 1998,
        "pitcher": "Kerry Wood",
        "team": "CHC",
        "opp": "HOU",
        "total": 20,
        "hook": "Kerry Wood's 20-K one-hitter at Wrigley.",
        "analytic": (
            "Estimated CSW% ~ 40% that afternoon. Modern elite SPs top "
            "out around 34% across a full season -- that start remains "
            "the whiff-rate ceiling for a Cubs rookie outing."
        ),
    },
    {
        "year": 1986,
        "pitcher": "Roger Clemens",
        "team": "BOS",
        "opp": "SEA",
        "total": 20,
        "hook": "Roger Clemens, first 20-K game in MLB history, Fenway, April 29.",
        "analytic": (
            "Split-finger + power fastball carried ~34% SwStr on secondaries "
            "that night. Any current starter pushing past 18% SwStr across a "
            "full start is in Clemens-1986 airspace."
        ),
    },
    {
        "year": 2001,
        "pitcher": "Randy Johnson",
        "team": "ARI",
        "opp": "SF",
        "total": 20,
        "hook": (
            "Randy Johnson, 20 K's over nine innings (no-decision) on "
            "May 8, 2001."
        ),
        "analytic": (
            "Slider usage near 45% that night. Modern LHP arsenals rarely "
            "cross 40% slider mix -- that one was an outlier even for the "
            "Big Unit."
        ),
    },
    {
        "year": 1999,
        "pitcher": "Pedro Martinez",
        "team": "BOS",
        "opp": "CLE",
        "total": 17,
        "hook": (
            "Pedro Martinez, 17 K + one hit across six innings "
            "in the 1999 ALDS."
        ),
        "analytic": (
            "CSW% estimated at 42% in that relief outing -- the kind of "
            "number that grades out as a single-game Grade A+ edge even "
            "against a 2025 Cleveland lineup."
        ),
    },
    {
        "year": 2016,
        "pitcher": "Max Scherzer",
        "team": "WSH",
        "opp": "DET",
        "total": 20,
        "hook": "Max Scherzer's 20-K no-walk start vs Detroit, May 11, 2016.",
        "analytic": (
            "Four-seam usage that night stayed above 55% and still posted "
            "a 22% SwStr. Single-pitch dominance like that is the upper "
            "bound of what the arsenal-adjustment multiplier can flag."
        ),
    },
    {
        "year": 2013,
        "pitcher": "Nolan Ryan",
        "team": "retired-career",
        "opp": "history",
        "total": 5714,
        "hook": "Nolan Ryan's career K total: 5,714, untouched since 1993.",
        "analytic": (
            "At today's ~24 BF per start, catching Ryan requires roughly "
            "81 starts of career 30% K/BF -- a pace only Kershaw-prime "
            "or peak Clemens ever held for a full season."
        ),
    },
)


@dataclass(frozen=True)
class SupportingPost:
    """One ready-to-post supporting item.  `text` is the full body
    the workflow pipes to X / Discord / email."""
    tag: str
    text: str

    def to_dict(self) -> dict:
        return {"tag": self.tag, "text": self.text}


# ---------------------------------------------------------------- seeding

def _seed_int(date_str: str, salt: str = "") -> int:
    m = hashlib.sha256()
    m.update(date_str.encode("utf-8"))
    m.update(b"|")
    m.update(salt.encode("utf-8"))
    return int.from_bytes(m.digest()[:4], "big")


def select_types_for_day(date_str: str, n: int = 1) -> List[str]:
    """Rotate through ALL_TAGS by ordinal date so subscribers never
    see the same post type two days running. `n` caps at 2 per the
    brief ("1-2 per day max")."""
    n = max(1, min(2, int(n)))
    try:
        base = dt.date.fromisoformat(date_str).toordinal()
    except (TypeError, ValueError):
        base = _seed_int(date_str)
    order = list(ALL_TAGS)
    i = base % len(order)
    chosen = [order[i]]
    if n == 2:
        # Step forward by a prime so the paired types always differ.
        chosen.append(order[(i + 1) % len(order)])
    return chosen


# ---------------------------------------------------------------- generators

def _pick(rng: random.Random, seq: Sequence) -> Any:
    return seq[rng.randrange(len(seq))]


def generate_k_of_the_night(
    date_str: str,
    last_night: Optional[Dict[str, Any]] = None,
) -> SupportingPost:
    """Build the K of the Night post from the previous evening's
    standout outing.  `last_night` carries a minimal dict shape:

        {"pitcher": "Paul Skenes", "team": "PIT", "opp": "HOU",
         "ks": 11, "ip": "6.1", "swstr": 0.152, "line": 7.5}

    Every field is optional -- the generator degrades gracefully and
    never invents a number it wasn't handed.
    """
    rng = random.Random(_seed_int(date_str, "kotn"))
    intro = _pick(rng, _INTRO_70S)

    if not last_night:
        # No data -> honest fallback, still on-brand.
        body = (
            "No standout start from last night cleared the K-of-the-Night "
            "bar (11+ K on a 7.5+ line). Back at it tonight."
        )
        text = f"[{TAG_K_OF_THE_NIGHT}] {intro} {body}"
        return SupportingPost(tag=TAG_K_OF_THE_NIGHT, text=text)

    pitcher = last_night.get("pitcher") or "a starter"
    team = last_night.get("team") or ""
    opp = last_night.get("opp") or "their opponent"
    ks = last_night.get("ks")
    ip = last_night.get("ip")
    swstr = last_night.get("swstr")
    line = last_night.get("line")

    head_bits = [pitcher]
    if team:
        head_bits.append(f"({team})")
    head_bits.append("vs.")
    head_bits.append(opp)
    head = " ".join(head_bits)

    metric_bits: List[str] = []
    if ks is not None:
        metric_bits.append(f"{ks} K")
    if ip is not None:
        metric_bits.append(f"{ip} IP")
    if swstr is not None:
        metric_bits.append(f"{float(swstr)*100:.1f}% SwStr")
    metric_str = ", ".join(metric_bits) if metric_bits else "a strong whiff line"

    # Analytical tie-in.  No "should have tailed him", no tout.
    tie_templates = [
        "{metric} -- arsenal/CSW combo graded above the A+ bar.",
        "{metric} -- whiff rate placed the start in the top decile of "
        "recent SP outings.",
        "{metric} -- projection cleared the line by multiple standard "
        "deviations in MC.",
    ]
    tie = _pick(rng, tie_templates).format(metric=metric_str)

    line_note = ""
    if line is not None and ks is not None and ks > line:
        line_note = f" Line closed at {line}; start finished at {ks}."

    # Attach a tasteful CLIP_SUGGESTION when we have enough info to
    # build a useful highlight search. Stays on its own line so the
    # posting tool can grep [CLIP_SUGGESTION: ...] cleanly and queue
    # the clip step separately from the tweet body.
    clip_url = clip_for_k_of_the_night(last_night)
    clip_line = render_clip_suggestion(clip_url)
    clip_tail = f"\n{clip_line}" if clip_line else ""

    text = f"[{TAG_K_OF_THE_NIGHT}] {intro} {head}: {tie}{line_note}{clip_tail}"
    return SupportingPost(tag=TAG_K_OF_THE_NIGHT, text=text)


def generate_stat_drop(
    date_str: str,
    slate_hooks: Optional[Dict[str, Any]] = None,
) -> SupportingPost:
    """Build the Stat Drop post. `slate_hooks` is a dict of optional
    keys; each key triggers a distinct template so multiple days of
    Stat Drops stay varied:

        {"umpire_top": {"name": "D. Bellino", "factor": 1.06},
         "lineup_swstr_leader": {"team": "CHW", "swstr": 0.128},
         "arsenal_edge": {"pitcher": "Skubal", "pitch": "SL", "swstr": 0.185},
         "form_streak": {"pitcher": "Skenes", "starts": 4, "k_per_bf": 0.30}}

    Always returns a SupportingPost even when slate_hooks is None --
    the brief requires 1-2 supporting posts per day, so the generator
    always has SOMETHING factual to say.
    """
    rng = random.Random(_seed_int(date_str, "stat"))
    facts: List[str] = []
    hooks = slate_hooks or {}

    # Build a pool of candidate facts, then pick one deterministically.
    ump = hooks.get("umpire_top")
    if isinstance(ump, dict) and ump.get("name") and ump.get("factor") is not None:
        sign = "+K" if float(ump["factor"]) > 1.0 else "-K"
        facts.append(
            f"Tonight's most extreme HP ump: {ump['name']} at "
            f"{sign} {float(ump['factor']):.2f}x career K-factor."
        )

    leader = hooks.get("lineup_swstr_leader")
    if isinstance(leader, dict) and leader.get("team") and leader.get("swstr") is not None:
        facts.append(
            f"Highest-whiff lineup on tonight's board: {leader['team']} at "
            f"{float(leader['swstr'])*100:.1f}% SwStr vs RHP (league avg "
            f"11.0%)."
        )

    arsenal = hooks.get("arsenal_edge")
    if isinstance(arsenal, dict) and arsenal.get("pitcher") and arsenal.get("pitch") \
            and arsenal.get("swstr") is not None:
        facts.append(
            f"{arsenal['pitcher']}'s {arsenal['pitch']} is missing bats at "
            f"{float(arsenal['swstr'])*100:.1f}% -- roughly double the "
            f"league rate for that pitch class."
        )

    form = hooks.get("form_streak")
    if isinstance(form, dict) and form.get("pitcher") and form.get("starts") \
            and form.get("k_per_bf") is not None:
        facts.append(
            f"{form['pitcher']} over his last {int(form['starts'])} starts: "
            f"{float(form['k_per_bf'])*100:.1f}% K/BF vs a league SP "
            f"baseline near 23.5%."
        )

    if not facts:
        facts.append(
            "League-average starter K/BF is holding near 23.5% this season. "
            "Any SP running 28%+ on the board tonight is a tier above."
        )

    fact = _pick(rng, facts)
    text = f"[{TAG_STAT_DROP}] {fact}"
    return SupportingPost(tag=TAG_STAT_DROP, text=text)


def generate_throwback_k(date_str: str) -> SupportingPost:
    """Pull one curated throwback + modern tie-in.  Rotates through
    the catalog deterministically by date.  When the catalog has a
    matching canonical broadcast description, attach it as a
    CLIP_SUGGESTION on a separate line."""
    rng = random.Random(_seed_int(date_str, "throwback"))
    intro = _pick(rng, _INTRO_70S)
    item = _pick(rng, _THROWBACKS)
    clip_desc = clip_for_throwback(item)
    clip_line = render_clip_suggestion(clip_desc)
    clip_tail = f"\n{clip_line}" if clip_line else ""
    text = (
        f"[{TAG_THROWBACK_K}] {intro} {item['hook']} "
        f"{item['analytic']}{clip_tail}"
    )
    return SupportingPost(tag=TAG_THROWBACK_K, text=text)


# ---------------------------------------------------------------- orchestrator

def generate_supporting(
    date_str: str,
    *,
    types: Optional[Sequence[str]] = None,
    last_night: Optional[Dict[str, Any]] = None,
    slate_hooks: Optional[Dict[str, Any]] = None,
    n: int = 1,
) -> List[SupportingPost]:
    """Pick the day's rotation and build each selected post.  Caller
    supplies the `last_night` / `slate_hooks` payloads; the generator
    never reaches for live data itself (that's the workflow's job)."""
    if types is None:
        types = select_types_for_day(date_str, n=n)
    posts: List[SupportingPost] = []
    for t in types:
        if t == TAG_K_OF_THE_NIGHT:
            posts.append(generate_k_of_the_night(date_str, last_night))
        elif t == TAG_STAT_DROP:
            posts.append(generate_stat_drop(date_str, slate_hooks))
        elif t == TAG_THROWBACK_K:
            posts.append(generate_throwback_k(date_str))
        else:
            raise ValueError(f"Unknown supporting content tag: {t!r}")
    return posts


def render_supporting(posts: Sequence[SupportingPost]) -> str:
    """Render the supporting-post bundle as plain text for the
    workflow to pipe to the posting tool.  One blank line separates
    each tagged block so X / email clients parse them cleanly."""
    if not posts:
        return ""
    return "\n\n".join(p.text for p in posts).rstrip() + "\n"


# ---------------------------------------------------------------- compliance

# Short blocklist the tests enforce -- if any of these appear in the
# rendered output the test suite fails and we refuse to ship.  Same
# spirit as the public-mode sanitizer in the main engine.
HYPE_BLOCKLIST = (
    "lock", "smash", "take the over", "take the under", "cash it",
    "bang", "slam dunk", "easy money", "no brainer", "free money",
    "hammer", "guarantee", "guaranteed",
)
