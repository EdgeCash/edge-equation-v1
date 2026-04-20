"""
Email publisher.

Builds a subject and body from a card payload. No SMTP calls. Dry-run
returns success immediately; non-dry-run simulates a send and returns
a fake message_id.
"""
from typing import Optional

from edge_equation.publishing.base_publisher import PublishResult


class EmailPublisher:

    def __init__(self, from_address: str = "edge@edgeequation.com"):
        self.from_address = from_address
        self._counter = 12345

    @staticmethod
    def build_subject(card: dict) -> str:
        card_type = card.get("card_type") or "card"
        # Prefer generated_at if present, else a generic placeholder
        date = (card.get("generated_at") or "").split("T")[0] or "today"
        return f"Edge Equation – {card_type} – {date}"

    @staticmethod
    def build_body(card: dict) -> str:
        headline = card.get("headline") or ""
        subhead = card.get("subhead") or ""
        tagline = card.get("tagline") or ""
        picks = card.get("picks") or []

        lines = []
        if headline:
            lines.append(headline)
        if subhead:
            lines.append(subhead)
        lines.append("")

        for p in picks:
            parts = [f"- {p.get('market_type', '?')}: {p.get('selection', '?')}"]
            grade = p.get("grade")
            if grade:
                parts.append(f"Grade: {grade}")
            if p.get("edge") is not None:
                parts.append(f"Edge: {p['edge']}")
            if p.get("kelly") is not None:
                parts.append(f"½ Kelly: {p['kelly']}")
            if p.get("fair_prob") is not None:
                parts.append(f"Fair Prob: {p['fair_prob']}")
            if p.get("expected_value") is not None:
                parts.append(f"Expected: {p['expected_value']}")
            lines.append(" | ".join(parts))

        if tagline:
            lines.append("")
            lines.append(tagline)
        return "\n".join(lines)

    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult:
        try:
            _ = self.build_subject(card_payload)
            _ = self.build_body(card_payload)
            if dry_run:
                return PublishResult(success=True, target="email", message_id="dry-run", error=None)
            fake_id = f"email-{self._counter}"
            return PublishResult(success=True, target="email", message_id=fake_id, error=None)
        except Exception as e:  # pragma: no cover — defensive
            return PublishResult(success=False, target="email", message_id=None, error=str(e))
