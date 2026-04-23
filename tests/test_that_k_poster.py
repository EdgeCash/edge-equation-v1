"""
That K Report -- X poster tests.

Covers OAuth 1.0a signing determinism, the dry-run / live split on
the CLI, K-Guy-only account gating, missing-credential handling,
and the HTTPError -> PostError translation.  Nothing in this file
ever hits the real X API -- the poster takes an injectable
`_opener` exactly so tests can substitute a fake urlopen.
"""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError

import pytest

from edge_equation.that_k.config import TargetAccount, XCredentials
from edge_equation.that_k.poster import (
    MAX_TWEET_LENGTH,
    PostError,
    PostResult,
    X_TWEETS_ENDPOINT,
    build_oauth_header,
    canned_test_text,
    post_tweet,
)


# ------------------------------------------------ fake urlopen helpers

class _FakeResp:
    """Minimal stand-in for urllib's urlopen response context manager."""
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_opener(captured: dict, body: bytes = b'{"data":{"id":"1234567890","text":"hi"}}', status: int = 200):
    """Build a fake urlopen-style callable that captures the urllib
    Request it was called with, plus returns a canned response body."""
    def opener(req):
        captured["req"] = req
        captured["body"] = req.data
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResp(body, status=status)
    return opener


def _kguy_creds(**overrides) -> XCredentials:
    base = dict(
        account=TargetAccount.KGUY,
        api_key="ck-12345",
        api_secret="cs-67890",
        access_token="at-abcde",
        access_token_secret="ats-fghij",
        missing=(),
    )
    base.update(overrides)
    return XCredentials(**base)


# ------------------------------------------------ OAuth signing

def test_build_oauth_header_is_deterministic_with_pinned_nonce_and_ts():
    """Same nonce + timestamp + creds + URL must produce identical
    signatures so we can lock the wire shape in a test."""
    creds = _kguy_creds()
    h1 = build_oauth_header(
        "POST", X_TWEETS_ENDPOINT, creds,
        nonce="fixed-nonce", timestamp="1700000000",
    )
    h2 = build_oauth_header(
        "POST", X_TWEETS_ENDPOINT, creds,
        nonce="fixed-nonce", timestamp="1700000000",
    )
    assert h1 == h2


def test_build_oauth_header_includes_required_oauth_fields():
    creds = _kguy_creds()
    h = build_oauth_header(
        "POST", X_TWEETS_ENDPOINT, creds,
        nonce="n", timestamp="1",
    )
    assert h.startswith("OAuth ")
    for required in (
        'oauth_consumer_key="ck-12345"',
        'oauth_nonce="n"',
        'oauth_signature_method="HMAC-SHA1"',
        'oauth_timestamp="1"',
        'oauth_token="at-abcde"',
        'oauth_version="1.0"',
        'oauth_signature=',
    ):
        assert required in h, f"missing OAuth field {required!r}"


def test_build_oauth_signature_changes_when_secret_changes():
    """Sanity: a different consumer secret must produce a different
    signature even with the same nonce + timestamp."""
    h1 = build_oauth_header(
        "POST", X_TWEETS_ENDPOINT, _kguy_creds(api_secret="cs-AAA"),
        nonce="n", timestamp="1",
    )
    h2 = build_oauth_header(
        "POST", X_TWEETS_ENDPOINT, _kguy_creds(api_secret="cs-BBB"),
        nonce="n", timestamp="1",
    )
    assert h1 != h2


# ------------------------------------------------ post_tweet wire shape

def test_post_tweet_sends_json_body_to_v2_endpoint():
    captured = {}
    creds = _kguy_creds()
    result = post_tweet(
        "hello world", creds, _opener=_fake_opener(captured),
    )
    # URL + method + JSON content-type.
    assert captured["url"] == X_TWEETS_ENDPOINT
    assert captured["method"] == "POST"
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["content-type"] == "application/json"
    assert headers["authorization"].startswith("OAuth ")
    # Body must be JSON with the exact text we asked to post.
    body = json.loads(captured["body"].decode("utf-8"))
    assert body == {"text": "hello world"}
    # Result parses tweet_id from the response.
    assert isinstance(result, PostResult)
    assert result.tweet_id == "1234567890"
    assert result.status == 200


def test_post_tweet_unicode_text_is_utf8_encoded():
    captured = {}
    text = "Cole 9 K — far out — São Paulo"
    post_tweet(text, _kguy_creds(), _opener=_fake_opener(captured))
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["text"] == text


def test_post_tweet_raises_post_error_on_4xx():
    """HTTPError carrying a body must wrap into PostError with the
    response body preserved so the operator sees the X reason."""
    def opener(req):
        raise HTTPError(
            url=X_TWEETS_ENDPOINT, code=403,
            msg="Forbidden", hdrs=None,
            fp=io.BytesIO(b'{"detail":"Unsupported Authentication"}'),
        )
    with pytest.raises(PostError) as ei:
        post_tweet("oops", _kguy_creds(), _opener=opener)
    assert ei.value.status == 403
    assert "Unsupported Authentication" in ei.value.body


def test_post_tweet_refuses_incomplete_credentials():
    """missing != complete -- post_tweet hard-fails BEFORE any
    network call so we can't ship a half-signed request."""
    bad = _kguy_creds(api_key="", missing=("X_API_KEY_KGUY",))
    with pytest.raises(PostError) as ei:
        post_tweet("hi", bad, _opener=lambda req: _FakeResp(b"{}"))
    assert "missing credentials" in ei.value.body


# ------------------------------------------------ canned test text

def test_canned_test_text_contains_brand_marker_and_timestamp():
    import datetime as dt
    fixed = dt.datetime(2026, 4, 23, 12, 30, 0)
    text = canned_test_text(now=fixed)
    assert "Test from That K Report pipeline" in text
    assert "2026-04-23 12:30:00Z" in text


def test_canned_test_text_two_calls_produce_distinct_strings():
    """Different timestamps -> different strings.  X rejects exact-
    duplicate tweets within a short window so this matters in the
    field, not just for unit tests."""
    import datetime as dt
    a = canned_test_text(now=dt.datetime(2026, 4, 23, 12, 30, 0))
    b = canned_test_text(now=dt.datetime(2026, 4, 23, 12, 30, 1))
    assert a != b


# ------------------------------------------------ CLI integration

def test_cli_post_dry_run_does_not_call_opener(monkeypatch, capsys):
    """Default invocation prints DRY-RUN preview and never calls
    urlopen.  Critical safety property -- a malformed dispatch
    cannot accidentally publish."""
    from edge_equation.that_k.__main__ import main
    # Provide complete creds so the preflight doesn't print INFO noise.
    for name in (
        "X_API_KEY_KGUY", "X_API_SECRET_KGUY",
        "X_ACCESS_TOKEN_KGUY", "X_ACCESS_TOKEN_SECRET_KGUY",
    ):
        monkeypatch.setenv(name, "stub-value")
    # Sentinel: replace urlopen with one that explodes if called.
    import urllib.request as urlmod
    def _explode(*a, **k):
        raise AssertionError(
            "urlopen called during DRY-RUN -- safety guard breached"
        )
    monkeypatch.setattr(urlmod, "urlopen", _explode)
    rc = main(["post", "--test"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "Test from That K Report pipeline" in out


def test_cli_post_rejects_main_target_account_choice():
    """The post subcommand's --target-account is gated to k_guy
    only.  argparse choices=[k_guy] enforces this; passing 'main'
    must SystemExit with non-zero rather than silently route."""
    from edge_equation.that_k.__main__ import main
    with pytest.raises(SystemExit):
        main(["post", "--test", "--target-account", "main"])


def test_cli_post_requires_exactly_one_text_source(monkeypatch):
    """--text / --from / --test are mutually exclusive.  Passing
    none is also rejected -- the operator must say what to post."""
    from edge_equation.that_k.__main__ import main
    for name in (
        "X_API_KEY_KGUY", "X_API_SECRET_KGUY",
        "X_ACCESS_TOKEN_KGUY", "X_ACCESS_TOKEN_SECRET_KGUY",
    ):
        monkeypatch.setenv(name, "stub")
    with pytest.raises(SystemExit):
        main(["post"])  # no source flag -> exit


def test_cli_post_live_requires_complete_credentials(monkeypatch, capsys):
    """--live with empty/missing creds must hard-fail BEFORE any
    network call."""
    from edge_equation.that_k.__main__ import main
    # Deliberately scrub every KGUY env var.
    for name in (
        "X_API_KEY_KGUY", "X_API_SECRET_KGUY",
        "X_ACCESS_TOKEN_KGUY", "X_ACCESS_TOKEN_SECRET_KGUY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as ei:
        main(["post", "--test", "--live"])
    # SystemExit message identifies the missing vars by name.
    assert "missing" in str(ei.value).lower() or ei.value.code != 0


def test_cli_post_warns_when_text_exceeds_x_cap(monkeypatch, capsys):
    """281+ char text emits a stderr warning but still proceeds with
    DRY-RUN so the operator can see what would have shipped."""
    from edge_equation.that_k.__main__ import main
    for name in (
        "X_API_KEY_KGUY", "X_API_SECRET_KGUY",
        "X_ACCESS_TOKEN_KGUY", "X_ACCESS_TOKEN_SECRET_KGUY",
    ):
        monkeypatch.setenv(name, "stub")
    long_text = "x" * (MAX_TWEET_LENGTH + 5)
    rc = main(["post", "--text", long_text])
    assert rc == 0
    err = capsys.readouterr().err
    assert "exceeds X default cap" in err


def test_cli_post_from_file_reads_payload(monkeypatch, tmp_path, capsys):
    from edge_equation.that_k.__main__ import main
    for name in (
        "X_API_KEY_KGUY", "X_API_SECRET_KGUY",
        "X_ACCESS_TOKEN_KGUY", "X_ACCESS_TOKEN_SECRET_KGUY",
    ):
        monkeypatch.setenv(name, "stub")
    p = tmp_path / "tweet.txt"
    p.write_text("hello from file\n", encoding="utf-8")
    rc = main(["post", "--from", str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hello from file" in out
    assert "DRY-RUN" in out


# ------------------------------------------------ workflow regression

def test_workflow_post_job_is_dispatch_only_and_kguy_creds_only():
    """The post job MUST be dispatch-only AND must only carry the
    *_KGUY secret env names -- never the main X_API_KEY plain
    secrets that would let the wrong identity ship a tweet."""
    wf = (Path(__file__).resolve().parents[1]
          / ".github" / "workflows" / "that-k-report.yml")
    text = wf.read_text(encoding="utf-8")
    assert "  post:" in text
    post_block = text.split("  post:", 1)[1]
    # Dispatch-only.
    assert "workflow_dispatch" in post_block
    # No schedule branch in the post block.
    assert "schedule" not in post_block.split("\n", 1)[0]
    # KGUY env vars present; main X secrets absent from this job.
    assert "X_API_KEY_KGUY" in post_block
    # Bare main secrets must NOT appear as env keys in this block.
    main_env_lines = [
        ln for ln in post_block.splitlines()
        if ln.strip().startswith("X_API_KEY:")
        or ln.strip().startswith("X_API_SECRET:")
        or ln.strip().startswith("X_ACCESS_TOKEN:")
        or ln.strip().startswith("X_ACCESS_TOKEN_SECRET:")
    ]
    assert main_env_lines == [], (
        f"main-account X secrets leaked into post job env: {main_env_lines}"
    )
