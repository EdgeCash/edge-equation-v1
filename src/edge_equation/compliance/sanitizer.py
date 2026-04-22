"""
Public-mode sanitizer.

Removes fields and substrings that must never appear in a Phase-1 public
card: edge numbers, Kelly sizing, "pick" language, anything that signals
sports-betting advice. The dict sanitizer operates on the card payload
PostingFormatter.build_card produces; the text sanitizer operates on the
rendered string we hand to the publisher.

Both are deterministic, idempotent, and additive -- they never synthesize
new content, only strip existing content.
"""
import re
from typing import Any, Dict, Iterable, List, Optional


# Pick fields that MUST be stripped from any public-mode payload.
FORBIDDEN_PICK_FIELDS = (
    "edge",
    "kelly",
    "kelly_breakdown",
)


# Card-summary fields that likewise must not reach the public surface.
FORBIDDEN_SUMMARY_FIELDS = (
    "edge",
    "kelly",
)


# Substrings in rendered text that indicate a sanitizer leak. Matching is
# word-boundary (case-insensitive) to avoid false positives like
# "wedge" -> "edge".
_LEAK_PATTERNS = [
    # "edge 0.049" / "edge: 0.05" / "edge 4.92%" / "edge 5%". Requires a
    # DECIMAL or PERCENT after "edge" so the Phase 24 Ledger recap's
    # "Daily Edge: 3 projections posted" section-label line doesn't
    # false-positive as a leak. A plain integer next to "edge" isn't
    # realistic premium-numbers leakage in our rendered output.
    re.compile(
        r"\bedge\s*[:=]?\s*[-+]?(?:\d+\.\d+%?|\d+%)",
        re.IGNORECASE,
    ),
    re.compile(r"\bhalf[- ]?kelly\b", re.IGNORECASE),
    re.compile(r"\bkelly\b", re.IGNORECASE),
    re.compile(r"\+EV\b", re.IGNORECASE),
    re.compile(r"\bexpected\s*value\b", re.IGNORECASE),
]


class PublicModeSanitizer:
    """
    Deterministic strip-only sanitizer:
    - sanitize_pick(pick_dict)   -> dict (copy) with forbidden fields dropped
    - sanitize_card(card_dict)   -> dict (copy) with picks + summary cleaned
    - sanitize_text(text)        -> str  (forbidden leak patterns redacted)
    - list_leaks(text)           -> list[str] (patterns that hit)
    """

    @staticmethod
    def sanitize_pick(pick: Dict[str, Any]) -> Dict[str, Any]:
        if not pick:
            return {}
        out = {k: v for k, v in pick.items() if k not in FORBIDDEN_PICK_FIELDS}
        # Remove the raw_universal_sum audit field from metadata in public mode.
        meta = out.get("metadata")
        if isinstance(meta, dict):
            scrubbed = {k: v for k, v in meta.items() if k != "raw_universal_sum"}
            out["metadata"] = scrubbed
        return out

    @staticmethod
    def sanitize_card(card: Dict[str, Any]) -> Dict[str, Any]:
        if not card:
            return {}
        out = dict(card)
        picks = out.get("picks") or []
        out["picks"] = [PublicModeSanitizer.sanitize_pick(p) for p in picks]
        summary = out.get("summary")
        if isinstance(summary, dict):
            out["summary"] = {
                k: v for k, v in summary.items() if k not in FORBIDDEN_SUMMARY_FIELDS
            }
        return out

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Redact any leaks with a placeholder without altering surrounding prose."""
        if not text:
            return text or ""
        out = text
        for pat in _LEAK_PATTERNS:
            out = pat.sub("[redacted]", out)
        return out

    @staticmethod
    def list_leaks(text: str) -> List[str]:
        if not text:
            return []
        hits: List[str] = []
        for pat in _LEAK_PATTERNS:
            hits.extend(pat.findall(text))
        return hits
