import httpx
import pytest

from edge_equation.publishing.discord_publisher import (
    DiscordPublisher,
    ENV_WEBHOOK_URL,
)


WEBHOOK = "https://discord.com/api/webhooks/1234/test_token"


def _card():
    return {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A",
             "edge": "0.049167", "kelly": "0.0324", "fair_prob": "0.553412"},
            {"market_type": "Total", "selection": "Over 9.5", "grade": "C",
             "expected_value": "9.78"},
        ],
        "tagline": "Facts. Not Feelings.",
    }


def _pub(**overrides):
    kwargs = {"webhook_url": WEBHOOK, "failsafe": False}
    kwargs.update(overrides)
    return DiscordPublisher(**kwargs)


def _success_client(capture=None):
    if capture is None:
        capture = {}
    def handler(request):
        capture["url"] = str(request.url)
        capture["method"] = request.method
        capture["body"] = request.content.decode()
        capture["headers"] = dict(request.headers)
        return httpx.Response(200, json={"id": "1122334455", "type": 0})
    return httpx.Client(transport=httpx.MockTransport(handler)), capture


def _error_client(status: int, body: dict = None):
    def handler(request):
        return httpx.Response(status, json=body or {"message": "err"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _204_client():
    def handler(request):
        return httpx.Response(204)
    return httpx.Client(transport=httpx.MockTransport(handler))


# -------------------------------------------------------------------- dry-run


def test_dry_run_success():
    pub = _pub()
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "discord"
    assert result.message_id == "dry-run"
    assert result.error is None
    assert result.failsafe_triggered is False


def test_dry_run_without_webhook_still_succeeds():
    pub = DiscordPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.message_id == "dry-run"


# ----------------------------------------------------------- embed structure


def test_embed_structure():
    embed = DiscordPublisher.build_embed(_card())
    assert "embeds" in embed and len(embed["embeds"]) == 1
    e = embed["embeds"][0]
    assert e["title"] == "Daily Edge"
    assert e["description"] == "Today's plays."
    assert e["footer"]["text"] == "Facts. Not Feelings."
    assert len(e["fields"]) == 2


def test_embed_handles_empty_picks():
    embed = DiscordPublisher.build_embed({"headline": "h", "subhead": "s", "tagline": "t", "picks": []})
    assert embed["embeds"][0]["fields"] == []


def test_embed_field_contains_edge_and_kelly():
    embed = DiscordPublisher.build_embed(_card())
    ml_field = next(f for f in embed["embeds"][0]["fields"] if "ML" in f["name"])
    assert "Edge:" in ml_field["value"]
    assert "½ Kelly:" in ml_field["value"]
    assert "Fair:" in ml_field["value"]


# ------------------------------------------------------------- credentials


def test_missing_webhook_returns_failure_non_dry_run():
    pub = DiscordPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.target == "discord"
    assert "missing credentials" in (result.error or "")


def test_webhook_loaded_from_env(monkeypatch):
    monkeypatch.setenv(ENV_WEBHOOK_URL, WEBHOOK)
    pub = DiscordPublisher(failsafe=False)
    assert pub.webhook_url == WEBHOOK


def test_explicit_kwarg_overrides_env(monkeypatch):
    monkeypatch.setenv(ENV_WEBHOOK_URL, "https://env.webhook")
    pub = DiscordPublisher(webhook_url="https://kwarg.webhook", failsafe=False)
    assert pub.webhook_url == "https://kwarg.webhook"


# -------------------------------------------------------- real POST (mocked)


def test_non_dry_run_posts_to_webhook():
    client, capture = _success_client()
    pub = _pub(http_client=client)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.message_id == "discord-1122334455"
    assert capture["url"].startswith(WEBHOOK)
    assert capture["method"] == "POST"


def test_non_dry_run_sends_wait_query_param():
    client, capture = _success_client()
    _pub(http_client=client).publish_card(_card(), dry_run=False)
    assert "wait=true" in capture["url"]


def test_non_dry_run_sends_embed_body():
    import json as _json
    client, capture = _success_client()
    _pub(http_client=client).publish_card(_card(), dry_run=False)
    body = _json.loads(capture["body"])
    assert "embeds" in body
    assert body["embeds"][0]["title"] == "Daily Edge"


def test_204_response_returns_posted_sentinel():
    pub = _pub(http_client=_204_client())
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.message_id == "discord-posted"


def test_http_400_is_surfaced_as_failure():
    pub = _pub(http_client=_error_client(400, {"message": "Bad webhook"}))
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "400" in (result.error or "")


def test_http_429_rate_limit_surfaced():
    pub = _pub(http_client=_error_client(429, {"message": "rate limited"}))
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert "429" in (result.error or "")


def test_publish_card_never_raises_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("network down")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = _pub(http_client=client)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.error


# ------------------------------------------------------- failsafe integration


class _StubFailsafe:
    def __init__(self):
        self.calls = []

    def deliver(self, subject, body, target="discord", now=None):
        self.calls.append({"subject": subject, "body": body, "target": target})
        return f"stub:{len(self.calls)}"


def test_failsafe_fires_on_missing_webhook():
    fs = _StubFailsafe()
    pub = DiscordPublisher(failsafe=fs)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is False
    assert result.failsafe_triggered is True
    assert "Daily Edge" in fs.calls[0]["body"]


def test_failsafe_fires_on_http_error():
    fs = _StubFailsafe()
    pub = DiscordPublisher(
        webhook_url=WEBHOOK,
        http_client=_error_client(500, {"message": "server err"}),
        failsafe=fs,
    )
    result = pub.publish_card(_card(), dry_run=False)
    assert result.failsafe_triggered is True
    assert "500" in (result.error or "")


def test_failsafe_not_fired_on_success():
    fs = _StubFailsafe()
    client, _ = _success_client()
    pub = DiscordPublisher(webhook_url=WEBHOOK, http_client=client, failsafe=fs)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.failsafe_triggered is False
    assert fs.calls == []


def test_failsafe_after_failure_no_retry():
    hits = {"count": 0}
    def handler(request):
        hits["count"] += 1
        return httpx.Response(500, json={"message": "err"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pub = DiscordPublisher(webhook_url=WEBHOOK, http_client=client, failsafe=_StubFailsafe())
    pub.publish_card(_card(), dry_run=False)
    assert hits["count"] == 1


def test_failsafe_disabled_with_false():
    pub = DiscordPublisher(failsafe=False)
    result = pub.publish_card(_card(), dry_run=False)
    assert result.failsafe_triggered is False
    assert result.failsafe_detail is None
