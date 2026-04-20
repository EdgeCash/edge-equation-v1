#!/usr/bin/env bash
# apply_phase4bc.sh — writes Phase-4B+4C modules and tests, runs pytest.

set -euo pipefail

echo "=== Phase 4B+4C: writing publisher + premium modules and tests ==="

ROOT_DIR="$(pwd)"
SRC="$ROOT_DIR/src"
TESTS="$ROOT_DIR/tests"

mkdir -p "$SRC/edge_equation/publishing" "$SRC/edge_equation/premium" "$TESTS"
[ -f "$SRC/edge_equation/publishing/__init__.py" ] || touch "$SRC/edge_equation/publishing/__init__.py"
[ -f "$SRC/edge_equation/premium/__init__.py" ] || touch "$SRC/edge_equation/premium/__init__.py"


cat > "$SRC/edge_equation/publishing/base_publisher.py" << 'MODULE_EOF'
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
MODULE_EOF

cat > "$SRC/edge_equation/publishing/x_publisher.py" << 'MODULE_EOF'
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
MODULE_EOF

cat > "$SRC/edge_equation/publishing/discord_publisher.py" << 'MODULE_EOF'
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
MODULE_EOF

cat > "$SRC/edge_equation/publishing/email_publisher.py" << 'MODULE_EOF'
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
MODULE_EOF

cat > "$SRC/edge_equation/publishing/publish_runner.py" << 'MODULE_EOF'
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
MODULE_EOF

cat > "$SRC/edge_equation/premium/mc_simulator.py" << 'MODULE_EOF'
"""
Monte Carlo simulator.

Deterministic: given a fixed seed and inputs, outputs are identical
across runs. Uses stdlib random.Random seeded at construction.

simulate_binary(prob): draws Bernoulli trials and returns quantiles of
the running mean. Useful for ML-type fair probabilities.

simulate_total(mean, stdev): draws from a normal distribution clipped
at zero and returns p10/p50/p90/mean.
"""
from decimal import Decimal, ROUND_HALF_UP
import random


def _quantile(sorted_values: list, q: float) -> float:
    """Linear-interpolation quantile on a pre-sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # index = q * (n - 1)
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _to_decimal(x: float, places: str = "0.000001") -> Decimal:
    return Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


class MonteCarloSimulator:

    def __init__(self, seed: int = 42, iterations: int = 10000):
        if iterations <= 0:
            raise ValueError("iterations must be positive")
        self.seed = seed
        self.iterations = iterations

    def _rng(self) -> random.Random:
        """Fresh Random seeded for each simulate_* call — ensures determinism per call."""
        return random.Random(self.seed)

    def simulate_binary(self, prob) -> dict:
        """
        Simulate Bernoulli outcomes at the given probability and return
        quantiles of the running mean across iterations.

        Args:
            prob: fair probability (Decimal or float-compatible), 0 <= p <= 1
        Returns:
            dict with 'p10', 'p50', 'p90', 'mean' as Decimals (6 decimal places).
        """
        p = float(Decimal(str(prob)))
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"prob must be in [0, 1], got {p}")
        rng = self._rng()
        running = []
        hits = 0
        for i in range(1, self.iterations + 1):
            if rng.random() < p:
                hits += 1
            running.append(hits / i)
        sorted_running = sorted(running)
        mean_val = hits / self.iterations
        return {
            "p10": _to_decimal(_quantile(sorted_running, 0.10)),
            "p50": _to_decimal(_quantile(sorted_running, 0.50)),
            "p90": _to_decimal(_quantile(sorted_running, 0.90)),
            "mean": _to_decimal(mean_val),
        }

    def simulate_total(self, mean, stdev) -> dict:
        """
        Simulate totals from a normal(mean, stdev) clipped at zero.

        Args:
            mean:  Decimal or float-compatible
            stdev: Decimal or float-compatible (>= 0)
        Returns:
            dict with 'p10', 'p50', 'p90', 'mean' as Decimals (2 decimal places).
        """
        m = float(Decimal(str(mean)))
        s = float(Decimal(str(stdev)))
        if s < 0:
            raise ValueError(f"stdev must be non-negative, got {s}")
        rng = self._rng()
        samples = []
        for _ in range(self.iterations):
            x = rng.gauss(m, s)
            if x < 0:
                x = 0.0
            samples.append(x)
        sorted_samples = sorted(samples)
        mean_val = sum(samples) / len(samples)
        # Totals are rounded to 2 decimals to match rest of engine
        return {
            "p10": _to_decimal(_quantile(sorted_samples, 0.10), places="0.01"),
            "p50": _to_decimal(_quantile(sorted_samples, 0.50), places="0.01"),
            "p90": _to_decimal(_quantile(sorted_samples, 0.90), places="0.01"),
            "mean": _to_decimal(mean_val, places="0.01"),
        }
MODULE_EOF

cat > "$SRC/edge_equation/premium/premium_pick.py" << 'MODULE_EOF'
"""
PremiumPick.

Immutable wrapper around an existing Pick. Adds distribution quantiles
from the Monte Carlo simulator and a free-form notes field. Does not
modify the underlying Pick schema.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from edge_equation.engine.pick_schema import Pick


@dataclass(frozen=True)
class PremiumPick:
    base_pick: Pick
    p10: Optional[Decimal] = None
    p50: Optional[Decimal] = None
    p90: Optional[Decimal] = None
    mean: Optional[Decimal] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "base_pick": self.base_pick.to_dict(),
            "p10": str(self.p10) if self.p10 is not None else None,
            "p50": str(self.p50) if self.p50 is not None else None,
            "p90": str(self.p90) if self.p90 is not None else None,
            "mean": str(self.mean) if self.mean is not None else None,
            "notes": self.notes,
        }
MODULE_EOF

cat > "$SRC/edge_equation/premium/premium_formatter.py" << 'MODULE_EOF'
"""
Premium formatter.

Pure formatting from PremiumPick to a flat dict suitable for card payloads.
No I/O, no side effects.
"""
from edge_equation.premium.premium_pick import PremiumPick


def format_premium_pick(premium_pick: PremiumPick) -> dict:
    bp = premium_pick.base_pick
    return {
        "selection": bp.selection,
        "market_type": bp.market_type,
        "sport": bp.sport,
        "line": bp.line.to_dict(),
        "fair_prob": str(bp.fair_prob) if bp.fair_prob is not None else None,
        "expected_value": str(bp.expected_value) if bp.expected_value is not None else None,
        "edge": str(bp.edge) if bp.edge is not None else None,
        "grade": bp.grade,
        "kelly": str(bp.kelly) if bp.kelly is not None else None,
        "p10": str(premium_pick.p10) if premium_pick.p10 is not None else None,
        "p50": str(premium_pick.p50) if premium_pick.p50 is not None else None,
        "p90": str(premium_pick.p90) if premium_pick.p90 is not None else None,
        "mean": str(premium_pick.mean) if premium_pick.mean is not None else None,
        "notes": premium_pick.notes,
        "game_id": bp.game_id,
        "event_time": bp.event_time,
    }
MODULE_EOF

cat > "$SRC/edge_equation/premium/premium_cards.py" << 'MODULE_EOF'
"""
Premium card builders.

Builds structured payloads from a list of PremiumPick. Pure functions
— no network, no state. The tagline is shared with the standard
posting_formatter for consistency.
"""
from typing import Iterable

from edge_equation.premium.premium_pick import PremiumPick
from edge_equation.premium.premium_formatter import format_premium_pick
from edge_equation.posting.posting_formatter import TAGLINE


def _build(card_type: str, headline: str, subhead: str, premium_picks: Iterable[PremiumPick]) -> dict:
    picks_list = list(premium_picks)
    return {
        "card_type": card_type,
        "headline": headline,
        "subhead": subhead,
        "picks": [format_premium_pick(pp) for pp in picks_list],
        "tagline": TAGLINE,
    }


def build_premium_daily_edge_card(premium_picks) -> dict:
    return _build(
        card_type="premium_daily_edge",
        headline="Premium Daily Edge",
        subhead="Full distributions and model notes.",
        premium_picks=premium_picks,
    )


def build_premium_overseas_edge_card(premium_picks) -> dict:
    return _build(
        card_type="premium_overseas_edge",
        headline="Premium Overseas Edge",
        subhead="International slate with full distributions.",
        premium_picks=premium_picks,
    )
MODULE_EOF

cat > "$TESTS/test_publishing_base.py" << 'MODULE_EOF'
import pytest

from edge_equation.publishing.base_publisher import PublishResult


def test_publish_result_construction_and_to_dict():
    r = PublishResult(success=True, target="x", message_id="x-1", error=None)
    d = r.to_dict()
    assert d == {"success": True, "target": "x", "message_id": "x-1", "error": None}


def test_publish_result_failure():
    r = PublishResult(success=False, target="discord", message_id=None, error="timeout")
    d = r.to_dict()
    assert d["success"] is False
    assert d["error"] == "timeout"
    assert d["message_id"] is None


def test_publish_result_is_frozen():
    r = PublishResult(success=True, target="x")
    with pytest.raises(Exception):
        r.success = False
MODULE_EOF

cat > "$TESTS/test_publishing_x.py" << 'MODULE_EOF'
from edge_equation.publishing.x_publisher import XPublisher, MAX_LEN


def _card(headline="Daily Edge", n_picks=2):
    return {
        "card_type": "daily_edge",
        "headline": headline,
        "subhead": "Today's model-graded plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167"},
            {"market_type": "Total", "selection": "Over 9.5", "grade": "C", "edge": None},
        ][:n_picks],
        "tagline": "Facts. Not Feelings.",
    }


def test_x_publisher_dry_run():
    pub = XPublisher()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "x"
    assert result.message_id == "dry-run"
    assert result.error is None


def test_x_publisher_non_dry_run():
    pub = XPublisher()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.target == "x"
    assert result.message_id is not None
    assert result.message_id.startswith("x-")
    assert len(result.message_id) > len("x-")


def test_x_publisher_truncates_long_text():
    long_headline = "H" * 500
    pub = XPublisher()
    # Build text via the internal formatter and confirm truncation
    text = pub._format_text({"headline": long_headline, "picks": [], "tagline": ""})
    assert len(text) > MAX_LEN, "precondition: test setup must produce over-length text"
    truncated = pub._truncate(text, MAX_LEN)
    assert len(truncated) <= MAX_LEN
    assert truncated.endswith("…")


def test_x_publisher_short_text_not_truncated():
    pub = XPublisher()
    text = pub._format_text(_card())
    truncated = pub._truncate(text, MAX_LEN)
    assert truncated == text
    assert "…" not in truncated


def test_x_publisher_accepts_credentials():
    pub = XPublisher(api_key="k", api_secret="s")
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
MODULE_EOF

cat > "$TESTS/test_publishing_discord.py" << 'MODULE_EOF'
from edge_equation.publishing.discord_publisher import DiscordPublisher


def _card():
    return {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167", "kelly": "0.0324"},
        ],
        "tagline": "Facts. Not Feelings.",
    }


def test_discord_dry_run_success():
    pub = DiscordPublisher(webhook_url="https://example.invalid")
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "discord"
    assert result.message_id == "dry-run"
    assert result.error is None


def test_discord_non_dry_run_success():
    pub = DiscordPublisher()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.target == "discord"
    assert result.message_id.startswith("discord-")


def test_discord_embed_structure():
    pub = DiscordPublisher()
    embed = pub.build_embed(_card())
    assert "embeds" in embed and len(embed["embeds"]) == 1
    e = embed["embeds"][0]
    assert e["title"] == "Daily Edge"
    assert e["description"] == "Today's plays."
    assert e["footer"]["text"] == "Facts. Not Feelings."
    assert len(e["fields"]) == 1


def test_discord_handles_empty_picks_without_raising():
    pub = DiscordPublisher()
    card = {"headline": "h", "subhead": "s", "tagline": "t", "picks": []}
    result = pub.publish_card(card, dry_run=True)
    assert result.success is True
MODULE_EOF

cat > "$TESTS/test_publishing_email.py" << 'MODULE_EOF'
from edge_equation.publishing.email_publisher import EmailPublisher


def _card():
    return {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167",
             "kelly": "0.0324", "fair_prob": "0.618133"},
        ],
        "tagline": "Facts. Not Feelings.",
        "generated_at": "2026-04-20T09:00:00",
    }


def test_email_dry_run_success():
    pub = EmailPublisher()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "email"
    assert result.message_id == "dry-run"


def test_email_non_dry_run_returns_fake_id():
    pub = EmailPublisher()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.target == "email"
    assert result.message_id.startswith("email-")


def test_email_subject_includes_card_type_and_date():
    pub = EmailPublisher()
    subject = pub.build_subject(_card())
    assert "Edge Equation" in subject
    assert "daily_edge" in subject
    assert "2026-04-20" in subject


def test_email_body_contains_headline_and_tagline():
    pub = EmailPublisher()
    body = pub.build_body(_card())
    assert "Daily Edge" in body
    assert "Facts. Not Feelings." in body
    # Must reference the pick details
    assert "BOS" in body
    assert "Grade: A" in body


def test_email_custom_from_address():
    pub = EmailPublisher(from_address="custom@example.com")
    assert pub.from_address == "custom@example.com"
MODULE_EOF

cat > "$TESTS/test_publish_runner.py" << 'MODULE_EOF'
from datetime import datetime

from edge_equation.publishing.publish_runner import (
    publish_daily_edge,
    publish_evening_edge,
    publish_card,
)
from edge_equation.publishing.base_publisher import PublishResult


RUN = datetime(2026, 4, 20, 9, 0, 0)


def test_publish_daily_edge_dry_run_returns_three_results():
    results = publish_daily_edge(dry_run=True, run_datetime=RUN)
    assert len(results) == 3
    targets = {r.target for r in results}
    assert targets == {"x", "discord", "email"}
    for r in results:
        assert isinstance(r, PublishResult)
        assert r.success is True
        assert r.message_id == "dry-run"


def test_publish_evening_edge_dry_run_returns_three_results():
    results = publish_evening_edge(dry_run=True, run_datetime=RUN)
    assert len(results) == 3
    for r in results:
        assert r.success is True
        assert r.message_id == "dry-run"


def test_publish_daily_edge_non_dry_run_simulates():
    results = publish_daily_edge(dry_run=False, run_datetime=RUN)
    assert len(results) == 3
    for r in results:
        assert r.success is True
        # Simulated send returns a fake ID specific to target
        assert r.message_id is not None
        assert r.message_id != "dry-run"
        assert r.message_id.startswith(f"{r.target}-")


def test_publish_card_accepts_arbitrary_payload():
    # Verify the generic publish_card helper works for non-scheduler cards
    card = {
        "card_type": "custom",
        "headline": "Test",
        "subhead": "sub",
        "picks": [],
        "tagline": "Facts. Not Feelings.",
    }
    results = publish_card(card, dry_run=True)
    assert len(results) == 3
    for r in results:
        assert r.success is True
MODULE_EOF

cat > "$TESTS/test_premium_mc_simulator.py" << 'MODULE_EOF'
import pytest
from decimal import Decimal

from edge_equation.premium.mc_simulator import MonteCarloSimulator


def test_simulate_binary_is_deterministic_with_fixed_seed():
    sim1 = MonteCarloSimulator(seed=42, iterations=1000)
    sim2 = MonteCarloSimulator(seed=42, iterations=1000)
    r1 = sim1.simulate_binary(Decimal("0.6"))
    r2 = sim2.simulate_binary(Decimal("0.6"))
    assert r1 == r2


def test_simulate_binary_returns_expected_keys():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_binary(Decimal("0.6"))
    assert set(r.keys()) == {"p10", "p50", "p90", "mean"}
    for k, v in r.items():
        assert isinstance(v, Decimal)


def test_simulate_binary_mean_near_input_prob():
    sim = MonteCarloSimulator(seed=42, iterations=5000)
    r = sim.simulate_binary(Decimal("0.6"))
    # Mean should be within 5 percentage points of the true probability
    assert abs(float(r["mean"]) - 0.6) < 0.05


def test_simulate_binary_quantile_ordering():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_binary(Decimal("0.6"))
    assert r["p10"] <= r["p50"] <= r["p90"]


def test_simulate_binary_out_of_range_raises():
    sim = MonteCarloSimulator()
    with pytest.raises(ValueError):
        sim.simulate_binary(Decimal("-0.1"))
    with pytest.raises(ValueError):
        sim.simulate_binary(Decimal("1.5"))


def test_simulate_total_is_deterministic_with_fixed_seed():
    sim1 = MonteCarloSimulator(seed=42, iterations=1000)
    sim2 = MonteCarloSimulator(seed=42, iterations=1000)
    r1 = sim1.simulate_total(Decimal("10.0"), Decimal("1.5"))
    r2 = sim2.simulate_total(Decimal("10.0"), Decimal("1.5"))
    assert r1 == r2


def test_simulate_total_returns_expected_keys():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_total(Decimal("10.0"), Decimal("1.5"))
    assert set(r.keys()) == {"p10", "p50", "p90", "mean"}
    for v in r.values():
        assert isinstance(v, Decimal)


def test_simulate_total_mean_near_input():
    sim = MonteCarloSimulator(seed=42, iterations=5000)
    r = sim.simulate_total(Decimal("10.0"), Decimal("1.5"))
    # Mean should be close to the input mean
    assert abs(float(r["mean"]) - 10.0) < 0.2


def test_simulate_total_quantile_ordering():
    sim = MonteCarloSimulator(seed=42, iterations=1000)
    r = sim.simulate_total(Decimal("10.0"), Decimal("1.5"))
    assert r["p10"] <= r["p50"] <= r["p90"]


def test_simulate_total_negative_stdev_raises():
    sim = MonteCarloSimulator()
    with pytest.raises(ValueError):
        sim.simulate_total(Decimal("10.0"), Decimal("-1.0"))


def test_simulator_invalid_iterations_raises():
    with pytest.raises(ValueError):
        MonteCarloSimulator(iterations=0)
    with pytest.raises(ValueError):
        MonteCarloSimulator(iterations=-5)


def test_different_seeds_produce_different_outputs():
    sim1 = MonteCarloSimulator(seed=42, iterations=1000)
    sim2 = MonteCarloSimulator(seed=43, iterations=1000)
    r1 = sim1.simulate_binary(Decimal("0.6"))
    r2 = sim2.simulate_binary(Decimal("0.6"))
    # Means for 1000 samples should differ at 6-decimal precision with different seeds
    assert r1["mean"] != r2["mean"] or r1["p10"] != r2["p10"]
MODULE_EOF

cat > "$TESTS/test_premium_pick_and_formatter.py" << 'MODULE_EOF'
import pytest
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.premium.premium_pick import PremiumPick
from edge_equation.premium.premium_formatter import format_premium_pick


def _make_ml_pick():
    bundle = FeatureBuilder.build(
        sport="MLB",
        market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS",
        selection="BOS",
    )
    return BettingEngine.evaluate(bundle, Line(odds=-132))


def _make_premium_ml_pick():
    pick = _make_ml_pick()
    return PremiumPick(
        base_pick=pick,
        p10=Decimal("0.580000"),
        p50=Decimal("0.620000"),
        p90=Decimal("0.655000"),
        mean=Decimal("0.618000"),
        notes="Deterministic MC with 1000 iterations.",
    )


def test_premium_pick_wraps_base_pick():
    pp = _make_premium_ml_pick()
    assert pp.base_pick.fair_prob is not None
    assert pp.base_pick.selection == "BOS"
    assert pp.p10 == Decimal("0.580000")


def test_premium_pick_is_frozen():
    pp = _make_premium_ml_pick()
    with pytest.raises(Exception):
        pp.p50 = Decimal("0.9")


def test_premium_pick_to_dict():
    pp = _make_premium_ml_pick()
    d = pp.to_dict()
    assert d["base_pick"]["selection"] == "BOS"
    assert d["p10"] == "0.580000"
    assert d["p50"] == "0.620000"
    assert d["p90"] == "0.655000"
    assert d["mean"] == "0.618000"
    assert d["notes"] == "Deterministic MC with 1000 iterations."


def test_premium_pick_minimal_no_quantiles():
    pick = _make_ml_pick()
    pp = PremiumPick(base_pick=pick)
    d = pp.to_dict()
    assert d["p10"] is None
    assert d["p50"] is None
    assert d["p90"] is None
    assert d["mean"] is None
    assert d["notes"] is None


def test_format_premium_pick_returns_expected_keys():
    pp = _make_premium_ml_pick()
    out = format_premium_pick(pp)
    expected_keys = {
        "selection", "market_type", "sport", "line",
        "fair_prob", "expected_value", "edge", "grade", "kelly",
        "p10", "p50", "p90", "mean", "notes",
        "game_id", "event_time",
    }
    assert set(out.keys()) == expected_keys
    assert out["selection"] == "BOS"
    assert out["market_type"] == "ML"
    assert out["grade"] == "A"
    assert out["p50"] == "0.620000"
    assert out["notes"] == "Deterministic MC with 1000 iterations."


def test_format_premium_pick_values_match_base_pick():
    pp = _make_premium_ml_pick()
    out = format_premium_pick(pp)
    base = pp.base_pick
    assert out["fair_prob"] == str(base.fair_prob)
    assert out["edge"] == str(base.edge)
    assert out["kelly"] == str(base.kelly)
    assert out["game_id"] == base.game_id
MODULE_EOF

cat > "$TESTS/test_premium_cards.py" << 'MODULE_EOF'
from decimal import Decimal

from edge_equation.engine.feature_builder import FeatureBuilder
from edge_equation.engine.betting_engine import BettingEngine
from edge_equation.engine.pick_schema import Line
from edge_equation.premium.premium_pick import PremiumPick
from edge_equation.premium.premium_cards import (
    build_premium_daily_edge_card,
    build_premium_overseas_edge_card,
)


def _make_premium_picks():
    bundle = FeatureBuilder.build(
        sport="MLB", market_type="ML",
        inputs={"strength_home": 1.32, "strength_away": 1.15, "home_adv": 0.115},
        universal_features={"home_edge": 0.085},
        game_id="MLB-2026-04-20-DET-BOS", selection="BOS",
    )
    pick1 = BettingEngine.evaluate(bundle, Line(odds=-132))
    pp1 = PremiumPick(
        base_pick=pick1,
        p10=Decimal("0.580000"), p50=Decimal("0.620000"),
        p90=Decimal("0.655000"), mean=Decimal("0.618000"),
        notes="High-confidence ML.",
    )

    bundle2 = FeatureBuilder.build(
        sport="MLB", market_type="Total",
        inputs={"off_env": 1.18, "def_env": 1.07, "pace": 1.03, "dixon_coles_adj": 0.00},
        universal_features={},
        selection="Over 9.5",
    )
    pick2 = BettingEngine.evaluate(bundle2, Line(odds=-110, number=Decimal("9.5")))
    pp2 = PremiumPick(
        base_pick=pick2,
        p10=Decimal("9.50"), p50=Decimal("11.52"), p90=Decimal("13.50"),
        mean=Decimal("11.52"),
        notes="MC total with 15% stdev assumption.",
    )
    return [pp1, pp2]


def test_premium_daily_edge_card_structure():
    card = build_premium_daily_edge_card(_make_premium_picks())
    assert card["card_type"] == "premium_daily_edge"
    assert card["headline"] == "Premium Daily Edge"
    assert card["subhead"] == "Full distributions and model notes."
    assert card["tagline"] == "Facts. Not Feelings."
    assert len(card["picks"]) == 2
    # Each pick must have the distribution fields
    for p in card["picks"]:
        for k in ("p10", "p50", "p90", "mean", "notes"):
            assert k in p


def test_premium_overseas_edge_card_structure():
    card = build_premium_overseas_edge_card(_make_premium_picks())
    assert card["card_type"] == "premium_overseas_edge"
    assert card["headline"] == "Premium Overseas Edge"
    assert card["tagline"] == "Facts. Not Feelings."
    assert len(card["picks"]) == 2


def test_premium_cards_preserve_order():
    picks = _make_premium_picks()
    card = build_premium_daily_edge_card(picks)
    assert card["picks"][0]["market_type"] == "ML"
    assert card["picks"][1]["market_type"] == "Total"
    reversed_card = build_premium_daily_edge_card(list(reversed(picks)))
    assert reversed_card["picks"][0]["market_type"] == "Total"
    assert reversed_card["picks"][1]["market_type"] == "ML"


def test_premium_cards_empty_picks():
    card = build_premium_daily_edge_card([])
    assert card["picks"] == []
    assert card["tagline"] == "Facts. Not Feelings."
MODULE_EOF

echo "=== Phase 4B+4C files written. Running pytest ==="

if command -v pytest >/dev/null 2>&1; then
  if ! pytest -v; then
    echo ""
    echo "ERROR: tests failed." >&2
    exit 1
  fi
else
  echo "WARNING: pytest not installed. Skipping test run."
  echo "  (Tests were verified in sandbox before this script was generated.)"
fi

echo ""
echo "=== Phase 4B+4C complete ==="
