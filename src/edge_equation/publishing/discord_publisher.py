"""
Discord publisher.

Posts a card as a Discord webhook message. No OAuth, no bot account -- just
a webhook URL you paste from Server Settings -> Integrations -> Webhooks.

Config:
- DISCORD_WEBHOOK_URL      required unless webhook_url= is passed explicitly.

Each card renders into a rich embed (title, description, fields, footer).
Discord accepts up to 10 embeds per message and caps total embed payload at
6,000 chars -- our cards are comfortably under both.

Failsafe: same contract as XPublisher. On any primary-path failure (missing
webhook URL, HTTP error, transport error) the rendered embed body is handed
to the configured failsafe and the publisher does NOT retry.
"""
import os
from typing import Optional

import httpx

from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.failsafe import default_failsafe


ENV_WEBHOOK_URL = "DISCORD_WEBHOOK_URL"


class DiscordPublisher:
    """
    Real Discord webhook publisher:
    - publish_card(card, dry_run=False) -> PublishResult
    - build_embed(card)                 -> dict (embed payload, pure function)
    Constructor reads webhook URL from kwarg > env var. Injectable http_client
    keeps tests deterministic.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        failsafe: Optional[object] = None,
    ):
        self.webhook_url = webhook_url if webhook_url is not None else os.environ.get(ENV_WEBHOOK_URL)
        self._http_client = http_client
        if failsafe is None:
            self._failsafe = default_failsafe()
        elif failsafe is False:
            self._failsafe = None
        else:
            self._failsafe = failsafe

    @staticmethod
    def build_embed(card: dict) -> dict:
        """Compose a Discord webhook payload dict."""
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
            if p.get("fair_prob") is not None:
                value_parts.append(f"Fair: {p['fair_prob']}")
            if p.get("expected_value") is not None:
                value_parts.append(f"EV: {p['expected_value']}")
            fields.append({"name": name, "value": " · ".join(value_parts), "inline": False})

        return {
            "embeds": [{
                "title": headline,
                "description": subhead,
                "fields": fields,
                "footer": {"text": tagline},
            }],
        }

    def _fire_failsafe(self, card: dict, error: str) -> tuple:
        if self._failsafe is None:
            return False, None
        try:
            subject = f"[Edge Equation] Discord post failsafe triggered ({error[:80]})"
            body = (
                f"The primary Discord post failed with: {error}\n\n"
                f"Card payload:\n{card}\n"
            )
            detail = self._failsafe.deliver(subject=subject, body=body, target="discord")
            return True, detail
        except Exception as e:
            return False, f"failsafe itself failed: {e}"

    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult:
        try:
            embed = self.build_embed(card_payload)
        except Exception as e:
            return PublishResult(success=False, target="discord", error=f"embed error: {e}")

        if dry_run:
            return PublishResult(success=True, target="discord", message_id="dry-run")

        if not self.webhook_url:
            err = f"missing credentials: {ENV_WEBHOOK_URL}"
            fired, detail = self._fire_failsafe(card_payload, err)
            return PublishResult(
                success=False, target="discord", error=err,
                failsafe_triggered=fired, failsafe_detail=detail,
            )

        try:
            owns_client = self._http_client is None
            client = self._http_client if not owns_client else httpx.Client(timeout=30.0)
            try:
                # Discord returns 204 No Content on success; set wait=true to
                # get a JSON body with the message id.
                resp = client.post(
                    self.webhook_url,
                    params={"wait": "true"},
                    json=embed,
                    headers={"Content-Type": "application/json"},
                )
            finally:
                if owns_client:
                    client.close()

            if resp.status_code >= 400:
                err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                fired, detail = self._fire_failsafe(card_payload, err)
                return PublishResult(
                    success=False, target="discord", error=err,
                    failsafe_triggered=fired, failsafe_detail=detail,
                )
            # On success, Discord with wait=true returns JSON including "id".
            # Older/alternate responses may return 204 with empty body.
            msg_id: Optional[str] = None
            if resp.status_code == 204 or not resp.content:
                msg_id = "discord-posted"
            else:
                try:
                    data = resp.json()
                    mid = data.get("id")
                    msg_id = f"discord-{mid}" if mid else "discord-posted"
                except Exception:
                    msg_id = "discord-posted"
            return PublishResult(success=True, target="discord", message_id=msg_id)

        except Exception as e:
            err = str(e)
            fired, detail = self._fire_failsafe(card_payload, err)
            return PublishResult(
                success=False, target="discord", error=err,
                failsafe_triggered=fired, failsafe_detail=detail,
            )
