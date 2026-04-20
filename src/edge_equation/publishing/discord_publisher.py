"""
Discord publisher.

Builds an embed-style payload dict. No real HTTP. Dry-run returns success
immediately; non-dry-run simulates a send and returns a fake message_id.
"""
from typing import Optional

from edge_equation.publishing.base_publisher import PublishResult


class DiscordPublisher:

    def __init__(self, webhook_url: Optional[str] = None):
        # Webhook URL accepted but never used; no real HTTP calls in this PR.
        self.webhook_url = webhook_url
        self._counter = 12345

    @staticmethod
    def build_embed(card: dict) -> dict:
        """Compose a dict shaped like a Discord embed payload (not sent)."""
        headline = card.get("headline") or ""
        subhead = card.get("subhead") or ""
        tagline = card.get("tagline") or ""
        picks = card.get("picks") or []

        fields = []
        for p in picks:
            name = f"{p.get('market_type', '?')}: {p.get('selection', '?')}"
            value_parts = [f"Grade: {p.get('grade', 'C')}"]
            if p.get("edge") is not None:
                value_parts.append(f"Edge: {p['edge']}")
            if p.get("kelly") is not None:
                value_parts.append(f"½ Kelly: {p['kelly']}")
            fields.append({"name": name, "value": " · ".join(value_parts), "inline": False})

        return {
            "embeds": [{
                "title": headline,
                "description": subhead,
                "fields": fields,
                "footer": {"text": tagline},
            }],
        }

    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult:
        try:
            _ = self.build_embed(card_payload)  # validates we can build it
            if dry_run:
                return PublishResult(success=True, target="discord", message_id="dry-run", error=None)
            fake_id = f"discord-{self._counter}"
            return PublishResult(success=True, target="discord", message_id=fake_id, error=None)
        except Exception as e:  # pragma: no cover — defensive
            return PublishResult(success=False, target="discord", message_id=None, error=str(e))
