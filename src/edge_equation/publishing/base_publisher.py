"""
Publisher base layer.

Defines the PublishResult dataclass returned by every publisher, and the
BasePublisher protocol that all publishers must satisfy. No network I/O.
"""
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class PublishResult:
    """Result of a single publish_card call."""
    success: bool
    target: str
    message_id: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "target": self.target,
            "message_id": self.message_id,
            "error": self.error,
        }


class BasePublisher(Protocol):
    """
    Protocol for publishers. Implementations must:
    - Not perform any network I/O when dry_run is True.
    - Return a PublishResult regardless of success/failure (never raise).
    - Simulate posting in dry_run=False mode with a deterministic fake
      message_id; no real external I/O in this PR.
    """
    def publish_card(self, card_payload: dict, dry_run: bool = False) -> PublishResult: ...
