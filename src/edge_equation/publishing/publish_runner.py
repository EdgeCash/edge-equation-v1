"""
Publish runner.

Orchestrates publishing a card across all three publishers (X, Discord,
Email). Failures on one publisher do not abort the others — every
publisher is invoked and every result captured.
"""
from datetime import datetime
from typing import Optional

from edge_equation.engine.daily_scheduler import (
    generate_daily_edge_card,
    generate_evening_edge_card,
)
from edge_equation.publishing.base_publisher import PublishResult
from edge_equation.publishing.x_publisher import XPublisher
from edge_equation.publishing.discord_publisher import DiscordPublisher
from edge_equation.publishing.email_publisher import EmailPublisher


def _publish_all(card_payload: dict, dry_run: bool) -> list:
    publishers = [
        XPublisher(api_key="dummy", api_secret="dummy"),
        DiscordPublisher(webhook_url="https://example.invalid/webhook"),
        EmailPublisher(from_address="edge@edgeequation.com"),
    ]
    results = []
    for pub in publishers:
        try:
            result = pub.publish_card(card_payload, dry_run=dry_run)
        except Exception as e:  # pragma: no cover — publishers shouldn't raise
            target = getattr(pub, "__class__", type("x", (), {})).__name__.lower()
            result = PublishResult(success=False, target=target, message_id=None, error=str(e))
        results.append(result)
    return results


def publish_daily_edge(dry_run: bool = True, run_datetime: Optional[datetime] = None) -> list:
    run_dt = run_datetime or datetime.now()
    card = generate_daily_edge_card(run_dt)
    return _publish_all(card, dry_run=dry_run)


def publish_evening_edge(dry_run: bool = True, run_datetime: Optional[datetime] = None) -> list:
    run_dt = run_datetime or datetime.now()
    card = generate_evening_edge_card(run_dt)
    return _publish_all(card, dry_run=dry_run)


def publish_card(card_payload: dict, dry_run: bool = True) -> list:
    """Publish an arbitrary pre-built card (standard or premium) across all targets."""
    return _publish_all(card_payload, dry_run=dry_run)
