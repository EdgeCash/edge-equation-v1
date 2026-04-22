"""
Mandatory Phase-1 disclaimer text + injection helper.

Every analytical output shipped to X (and cross-posted to Discord / Email
when operating in public_mode) must carry this line verbatim. The
checker in compliance/checker.py refuses any payload that lacks it.
"""


DISCLAIMER_TEXT = (
    "Pure data from our hybrid ensemble model. "
    "Facts. Not Feelings. What you do with it is on you."
)


def inject_disclaimer(text: str, disclaimer: str = DISCLAIMER_TEXT) -> str:
    """
    Append the disclaimer to a text payload if it isn't already present.
    Idempotent -- calling this twice does not double the disclaimer.
    Preserves trailing whitespace shape by adding exactly one blank line
    between the original text and the disclaimer.
    """
    if text is None:
        return disclaimer
    if disclaimer in text:
        return text
    body = text.rstrip()
    if body:
        return f"{body}\n\n{disclaimer}"
    return disclaimer


def inject_into_card(card: dict, disclaimer: str = DISCLAIMER_TEXT) -> dict:
    """
    Mutate-free: return a copy of the card dict with the disclaimer appended
    to the 'tagline' field. If a tagline is already present, the disclaimer
    is appended on a new line unless it's already there.
    """
    if card is None:
        return {"tagline": disclaimer}
    out = dict(card)
    existing = out.get("tagline") or ""
    if disclaimer in existing:
        return out
    out["tagline"] = (
        f"{existing}\n{disclaimer}" if existing else disclaimer
    )
    return out
