"""
That K Report -- video clip suggestions.

Light, tasteful, tied to the analytical point.  The generator emits
one of two output shapes:

    [CLIP_SUGGESTION: <description>]
    [CLIP_SUGGESTION: <https://www.youtube.com/results?search_query=...>]

The YouTube search-URL form is used ONLY for live highlight lookups
(K of the Night) where we want the poster to attach a freshly-cached
clip.  Throwback classics ship a description instead of a URL since
the best cut is usually the canonical broadcast footage the poster
already has in a bookmark folder.

Nothing here fabricates a specific video-ID or asserts that a named
clip exists.  Search URLs are deterministic and safe; descriptions
are audit-friendly strings a human poster can resolve.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, Optional


CLIP_TAG = "CLIP_SUGGESTION"


# Throwback classics catalog, keyed on (pitcher, year, total).
# Each entry's clip field is a SHORT human description -- not a URL.
# Keeps the Throwback post trustworthy: the description is a fact
# the poster can verify ("this is the canonical broadcast footage")
# without us making up a specific URL that might break.
_THROWBACK_CLIPS: Dict[str, str] = {
    "Kerry Wood/1998/20":
        "WGN broadcast of the 20-K one-hitter (May 6, 1998).",
    "Roger Clemens/1986/20":
        "NESN broadcast of Clemens' first 20-K game at Fenway "
        "(April 29, 1986).",
    "Randy Johnson/2001/20":
        "Fox broadcast of Johnson's 20-K night vs SF (May 8, 2001).",
    "Pedro Martinez/1999/17":
        "FOX broadcast of Pedro's 17-K ALDS relief appearance "
        "(October 11, 1999).",
    "Max Scherzer/2016/20":
        "MASN broadcast of Scherzer's no-walk 20-K game "
        "(May 11, 2016).",
    "Nolan Ryan/2013/5714":
        "MLB Network's Nolan Ryan career K-total retrospective.",
}


def clip_for_throwback(entry: Dict[str, Any]) -> Optional[str]:
    """Return the description string (no URL) for a curated throwback
    catalog entry, or None when we don't have a canonical match."""
    if not entry:
        return None
    key = f"{entry.get('pitcher','')}/{entry.get('year','')}/{entry.get('total','')}"
    return _THROWBACK_CLIPS.get(key)


def _search_url(query: str) -> str:
    """Build a YouTube search URL -- the only URL pattern this module
    emits.  Deterministic + stable across YouTube's UI changes."""
    q = urllib.parse.quote_plus(query)
    return f"https://www.youtube.com/results?search_query={q}"


def clip_for_k_of_the_night(last_night: Dict[str, Any]) -> Optional[str]:
    """YouTube-search URL for yesterday's standout start, built from
    the payload the supporting generator already consumes.  Returns
    None when the caller didn't hand us enough info to produce a
    useful search query."""
    if not last_night:
        return None
    pitcher = (last_night.get("pitcher") or "").strip()
    if not pitcher:
        return None
    opp = (last_night.get("opp") or "").strip()
    ks = last_night.get("ks")
    # Query gives the poster a one-click path to the best available
    # highlight cut without us ever asserting a specific video URL
    # exists at time-of-post.
    bits = [pitcher, "strikeouts"]
    if opp:
        bits.append(f"vs {opp}")
    if ks is not None:
        bits.append(f"{ks} K")
    return _search_url(" ".join(bits))


def render_clip_suggestion(payload: Optional[str]) -> str:
    """Wrap a description or URL in the canonical `[CLIP_SUGGESTION:
    ...]` tag format the post tooling greps for.  Returns an empty
    string when there's nothing to suggest -- caller should skip
    emitting the line entirely in that case."""
    if not payload:
        return ""
    return f"[{CLIP_TAG}: {payload}]"
