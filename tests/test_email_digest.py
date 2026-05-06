"""Tests for the daily email digest builder + Resend send.

Covers:

  - Subject + body shape across the empty-card, single-sport, and
    multi-sport feeds.
  - HTML escapes special characters (&, <, >, ").
  - Body carries the audit-locked footer prefix when the feed
    footer does.
  - load_config() honours the EDGE_FEATURE_EMAIL_DIGEST flag and
    returns a soft `is_send_ready=False` when keys are missing.
  - send_digest() short-circuits with `skipped_reason` when
    config isn't ready.
  - send_digest() handles HTTP error responses without crashing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from edge_equation.exporters.email_digest import (
    Digest,
    DigestConfig,
    build_digest,
    load_config,
    send_digest,
)


# ---------------------------------------------------------------------------
# Synthetic feeds
# ---------------------------------------------------------------------------


def _empty_feed() -> dict:
    return {
        "date": "2026-05-06",
        "footer": "Picks shown only for games not yet started · Updated: 09:32 CDT",
        "picks": [],
        "parlays": {"game_results": [], "player_props": []},
        "wnba": {"picks": [], "parlays": {}},
        "nfl": {"picks": [], "parlays": {}},
        "ncaaf": {"picks": [], "parlays": {}},
    }


def _single_pick(sel: str, market: str = "MONEYLINE") -> dict:
    return {
        "id": "x",
        "selection": sel,
        "market_type": market,
        "fair_prob": "0.55",
        "edge": "0.04",
        "tier": "STRONG",
        "line": {"odds": -110, "number": None},
    }


def _single_parlay(n_legs: int = 3) -> dict:
    return {
        "id": "p1",
        "n_legs": n_legs,
        "combined_american_odds": 580,
        "combined_decimal_odds": 6.8,
        "fair_decimal_odds": 6.0,
        "joint_prob_corr": "0.18",
        "joint_prob_independent": "0.20",
        "implied_prob": "0.147",
        "edge_pp": "5.2",
        "ev_units": "0.10",
        "stake_units": 0.5,
        "note": "strict",
        "legs": [
            {"selection": "NYY ML", "market_type": "MONEYLINE",
             "line_odds": -120, "side_probability": "0.55",
             "tier": "STRONG"}
            for _ in range(n_legs)
        ],
    }


def _multisport_feed() -> dict:
    feed = _empty_feed()
    feed["picks"] = [
        _single_pick("NYY · Moneyline"),
        _single_pick(
            "Aaron Judge · Home Runs Over 0.5",
            market="PLAYER_PROP_HR",
        ),
    ]
    feed["parlays"] = {
        "game_results": [_single_parlay(3)],
        "player_props": [],
    }
    feed["wnba"]["picks"] = [
        _single_pick(
            "A'ja Wilson · Points Over 22.5",
            market="PLAYER_PROP_POINTS",
        ),
    ]
    feed["nfl"]["picks"] = []
    feed["ncaaf"]["picks"] = []
    return feed


# ---------------------------------------------------------------------------
# build_digest
# ---------------------------------------------------------------------------


def test_build_digest_empty_feed_subject_says_no_picks():
    d = build_digest(_empty_feed())
    assert "No qualifying plays today" in d.subject
    assert "No qualifying plays today" in d.text
    assert "No qualifying plays today" in d.html
    assert "Edge Equation" in d.subject


def test_build_digest_renders_pick_rows_in_text_and_html():
    d = build_digest(_multisport_feed())
    assert "Aaron Judge" in d.text
    assert "A&#x27;ja Wilson" in d.html or "A'ja Wilson" in d.html
    assert "MLB" in d.text
    assert "WNBA" in d.text


def test_build_digest_subject_counts_picks_and_parlays():
    d = build_digest(_multisport_feed())
    # 3 picks across MLB(2) + WNBA(1) + 1 parlay.
    assert "3 picks" in d.subject
    assert "1 parlay" in d.subject


def test_build_digest_html_escapes_specials():
    feed = _empty_feed()
    feed["picks"] = [_single_pick("Smith & Wesson <Special>")]
    d = build_digest(feed)
    assert "Smith &amp; Wesson &lt;Special&gt;" in d.html


def test_build_digest_includes_footer_text():
    feed = _empty_feed()
    feed["footer"] = (
        "Picks shown only for games not yet started · "
        "Updated: 09:32 CDT | Data as of 14:32 UTC"
    )
    d = build_digest(feed)
    assert "Picks shown only for games not yet started" in d.html
    assert "Picks shown only for games not yet started" in d.text


def test_build_digest_renders_parlay_legs_in_text():
    feed = _empty_feed()
    feed["parlays"]["game_results"] = [_single_parlay(2)]
    d = build_digest(feed)
    assert "2-leg parlay" in d.text
    assert "NYY ML" in d.text


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_disabled_when_flag_unset():
    cfg = load_config(env={})
    assert cfg.enabled is False
    assert cfg.is_send_ready is False


def test_load_config_enabled_with_keys():
    cfg = load_config(env={
        "EDGE_FEATURE_EMAIL_DIGEST": "on",
        "RESEND_API_KEY": "key",
        "RESEND_AUDIENCE_ID": "aud",
        "RESEND_FROM_ADDRESS": "Edge <picks@edgeequation.com>",
    })
    assert cfg.is_send_ready is True


def test_load_config_disabled_when_keys_missing_even_if_flag_on():
    cfg = load_config(env={"EDGE_FEATURE_EMAIL_DIGEST": "true"})
    assert cfg.enabled is True
    assert cfg.is_send_ready is False     # no API key, audience, or FROM


@pytest.mark.parametrize(
    "value,expected",
    [
        ("on", True), ("ON", True), ("yes", True),
        ("true", True), ("1", True),
        ("off", False), ("", False), ("maybe", False),
    ],
)
def test_load_config_flag_string_parsing(value: str, expected: bool):
    cfg = load_config(env={"EDGE_FEATURE_EMAIL_DIGEST": value})
    assert cfg.enabled is expected


# ---------------------------------------------------------------------------
# send_digest
# ---------------------------------------------------------------------------


def _digest() -> Digest:
    return Digest(subject="s", html="<p>h</p>", text="t")


def test_send_digest_skipped_when_disabled():
    result = send_digest(_digest(), DigestConfig(
        api_key=None, audience_id=None, from_address=None,
        reply_to=None, enabled=False,
    ))
    assert result["sent"] is False
    assert "EDGE_FEATURE_EMAIL_DIGEST" in result["skipped_reason"]
    assert result["error"] is None


def test_send_digest_skipped_when_enabled_but_keys_missing():
    result = send_digest(_digest(), DigestConfig(
        api_key=None, audience_id=None, from_address=None,
        reply_to=None, enabled=True,
    ))
    assert result["sent"] is False
    assert "RESEND_API_KEY" in result["skipped_reason"]


def test_send_digest_returns_error_on_resend_4xx():
    """When Resend returns an HTTP 4xx, send_digest reports the
    error string rather than crashing."""
    fake = MagicMock()
    fake.post.side_effect = [
        _fake_resp(400, {"message": "audience not found"}),
    ]
    cfg = DigestConfig(
        api_key="k", audience_id="a",
        from_address="from@x.com", reply_to=None, enabled=True,
    )
    result = send_digest(_digest(), cfg, http_client=fake)
    assert result["sent"] is False
    assert result["error"] is not None
    assert "audience not found" in result["error"]


def test_send_digest_calls_create_then_send_endpoints():
    fake = MagicMock()
    fake.post.side_effect = [
        _fake_resp(200, {"id": "broadcast-123"}),
        _fake_resp(200, {"id": "broadcast-123"}),
    ]
    cfg = DigestConfig(
        api_key="k", audience_id="a",
        from_address="from@x.com", reply_to=None, enabled=True,
    )
    result = send_digest(_digest(), cfg, http_client=fake)
    assert result["sent"] is True
    assert result["broadcast_id"] == "broadcast-123"
    # Two POSTs: create broadcast + trigger send.
    assert fake.post.call_count == 2


def test_send_digest_propagates_send_step_failure():
    fake = MagicMock()
    fake.post.side_effect = [
        _fake_resp(200, {"id": "broadcast-456"}),
        _fake_resp(500, {"message": "send queue down"}),
    ]
    cfg = DigestConfig(
        api_key="k", audience_id="a",
        from_address="from@x.com", reply_to=None, enabled=True,
    )
    result = send_digest(_digest(), cfg, http_client=fake)
    assert result["sent"] is False
    assert result["broadcast_id"] == "broadcast-456"
    assert "send queue down" in result["error"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_resp(status: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = str(body)
    return resp
