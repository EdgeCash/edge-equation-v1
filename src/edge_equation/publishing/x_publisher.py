"""
X (Twitter) publisher.

Builds a short text representation of a card and "posts" it. No real
network calls — dry_run returns immediately; non-dry_run simulates a
post and returns a deterministic fake message_id.
"""
from typing import Optional

from edge_equation.publishing.base_publisher import PublishResult


MAX_LEN = 280


class XPublisher:

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        # Credentials accepted but never used; no real API calls in this PR.
        self.api_key = api_key
        self.api_secret = api_secret
        self._counter = 12345  # deterministic fake message_id seed

    @staticmethod
    def _format_text(card: dict) -> str:
        """Render a card as a single text post."""
        headline = card.get("headline") or ""
        picks = card.get("picks") or []
        tagline = card.get("tagline") or ""

        # Summarize the top pick (or two) compactly
        lines = [f"🎯 {headline}"]
        for p in picks[:2]:
            selection = p.get("selection") or "?"
            market = p.get("market_type") or ""
            grade = p.get("grade") or ""
            edge = p.get("edge")
            edge_str = f" edge {edge}" if edge else ""
            lines.append(f"• {market}: {selection} [{grade}]{edge_str}")
        if tagline:
            lines.append(tagline)
        return "\n".join(lines)

    @staticmethod
    def _truncate(text: str, limit: int = MAX_LEN) -> str:
        if len(text) <= limit:
            return text
        # Leave room for an ellipsis
        return text[: limit - 1].rstrip() + "…"

    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult:
        try:
            text = self._format_text(card_payload)
            text = self._truncate(text, MAX_LEN)
            if len(text) > MAX_LEN:
                # Belt-and-suspenders: should never happen after _truncate
                return PublishResult(
                    success=False, target="x", message_id=None,
                    error=f"text too long after truncation ({len(text)} chars)"
                )
            if dry_run:
                return PublishResult(success=True, target="x", message_id="dry-run", error=None)
            # Non-dry-run path: simulate a post
            fake_id = f"x-{self._counter}"
            return PublishResult(success=True, target="x", message_id=fake_id, error=None)
        except Exception as e:  # pragma: no cover — defensive
            return PublishResult(success=False, target="x", message_id=None, error=str(e))
