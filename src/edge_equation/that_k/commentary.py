"""
That K Report -- 70s-flair commentary buckets.

Maps a hit-rate number to a short, light commentary line tied to
the day's actual W-L.  Phrases are curated so repeat buckets rotate
through variants (deterministic from the date/seed key) -- same day
always reproduces the same line for auditability.

The main rule the brief enforces: "Always tie commentary back to
actual stats/ledger." So every generated line ends with a numeric
tail like `(4-3, 57% hit rate)` so subscribers see the math
alongside the flair.

Brand rule: analytical body of projections stays CLEAN. This module
only gets invoked on Results footers and supporting-content intros.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


# ------------------------------ buckets
#
# Losing side (mirrors the brief's "Losing/rough days" language):
#   <35%           -- brutal
#   35% - 47%      -- rough
#   48% - 54%      -- mild miss
# Winning side ("Winning days"):
#   55% - 64%      -- groovy
#   65% - 79%      -- far out
#   80%+           -- outta sight

_BRUTAL = (
    "Well, that was a real drag",
    "Real drag of a night",
    "Drag city",
)
_ROUGH = (
    "Tough night in the basement",
    "The vibes were off",
    "Basement kind of night",
)
_MILD_MISS = (
    "That one was for the birds",
    "Bit of a bird bath",
    "Call that one for the birds",
)
_GROOVY = (
    "That was groovy",
    "Groovy night",
    "Solid groove on tonight's card",
)
_FAR_OUT = (
    "Far out, man",
    "Far out",
    "Right on, far out numbers",
)
_OUTTA_SIGHT = (
    "Outta sight!",
    "We were cooking with gas tonight",
    "Outta sight -- cooking with gas",
)


BucketName = str  # "brutal" | "rough" | "mild_miss" | "groovy" | "far_out" | "outta_sight"


def _seed_int(*parts: object) -> int:
    m = hashlib.sha256()
    for p in parts:
        m.update(str(p).encode("utf-8"))
        m.update(b"|")
    return int.from_bytes(m.digest()[:4], "big")


def _bucket_for_hit_rate(hit_rate: float) -> Tuple[BucketName, Sequence[str]]:
    """Pick the bucket based on the measured hit rate.  Threshold
    edges match the brief verbatim."""
    if hit_rate >= 0.80:
        return "outta_sight", _OUTTA_SIGHT
    if hit_rate >= 0.65:
        return "far_out", _FAR_OUT
    if hit_rate >= 0.55:
        return "groovy", _GROOVY
    if hit_rate >= 0.48:
        return "mild_miss", _MILD_MISS
    if hit_rate >= 0.35:
        return "rough", _ROUGH
    return "brutal", _BRUTAL


def pick_phrase(
    hit_rate: float,
    seed_key: str = "",
) -> Tuple[BucketName, str]:
    """Return the (bucket_name, chosen_phrase) pair.  Deterministic
    rotation within the bucket keyed to seed_key so a given (date,
    hit_rate) always reproduces the same line."""
    name, variants = _bucket_for_hit_rate(hit_rate)
    if not variants:
        return name, ""
    rng = random.Random(_seed_int("thatk-commentary", seed_key, name, len(variants)))
    phrase = variants[rng.randrange(len(variants))]
    return name, phrase


@dataclass(frozen=True)
class Commentary:
    bucket: BucketName
    phrase: str
    text: str         # full rendered sentence with stats tail

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "phrase": self.phrase,
            "text": self.text,
        }


def render_day_commentary(
    wins: int,
    losses: int,
    pushes: int = 0,
    seed_key: str = "",
) -> Optional[Commentary]:
    """Compose a single-line commentary for the day's batch.  Returns
    None when there's nothing settled to comment on (W+L == 0) --
    the caller skips the footer in that case rather than force a
    flat sentence.

    Output shape:

        "<phrase> -- today went 4-3 (57% hit rate)."

    The " -- " separator + trailing stats tail is fixed so
    downstream parsers can reliably split the phrase from the
    numbers if they want to.
    """
    graded = wins + losses
    if graded == 0:
        return None
    hit_rate = wins / graded
    bucket, phrase = pick_phrase(hit_rate, seed_key=seed_key)
    push_part = f" · {pushes} push" if pushes else ""
    text = (
        f"{phrase} -- today went {wins}-{losses} "
        f"({hit_rate*100:.0f}% hit rate){push_part}."
    )
    return Commentary(bucket=bucket, phrase=phrase, text=text)


def render_season_commentary(
    wins: int,
    losses: int,
    pushes: int = 0,
    seed_key: str = "",
) -> Optional[Commentary]:
    """Same shape as render_day_commentary but phrased for the
    cumulative ledger snapshot (no "today" framing)."""
    graded = wins + losses
    if graded == 0:
        return None
    hit_rate = wins / graded
    bucket, phrase = pick_phrase(hit_rate, seed_key=seed_key or "season")
    push_part = f" · {pushes} push" if pushes else ""
    text = (
        f"{phrase} -- season ledger {wins}-{losses} "
        f"({hit_rate*100:.0f}% hit rate){push_part}."
    )
    return Commentary(bucket=bucket, phrase=phrase, text=text)
