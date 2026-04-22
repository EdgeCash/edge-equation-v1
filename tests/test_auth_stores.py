"""Tests for the auth persistence layer (users, tokens, sessions, subscriptions)."""
from datetime import datetime, timedelta
import pytest

from edge_equation.auth.sessions import COOKIE_NAME, Session, SessionStore
from edge_equation.auth.subscriptions import (
    ENTITLING_STATUSES,
    Subscription,
    SubscriptionStore,
)
from edge_equation.auth.tokens import AuthTokenStore, _hash_token
from edge_equation.auth.users import User, UserStore
from edge_equation.persistence.db import Database


@pytest.fixture
def conn():
    c = Database.open(":memory:")
    Database.migrate(c)
    yield c
    c.close()


# ------------------------------------------------- UserStore


def test_user_find_or_create_idempotent(conn):
    u1 = UserStore.find_or_create(conn, "bob@example.com")
    u2 = UserStore.find_or_create(conn, "bob@example.com")
    assert u1.user_id == u2.user_id


def test_user_email_normalized_lowercase(conn):
    u1 = UserStore.find_or_create(conn, "Bob@Example.com")
    u2 = UserStore.find_or_create(conn, "bob@example.com")
    assert u1.user_id == u2.user_id
    assert u1.email == "bob@example.com"


def test_user_empty_email_rejected(conn):
    with pytest.raises(ValueError, match="email required"):
        UserStore.find_or_create(conn, "   ")


def test_user_get_by_id_and_email(conn):
    u = UserStore.find_or_create(conn, "alice@example.com")
    assert UserStore.get_by_id(conn, u.user_id).email == "alice@example.com"
    assert UserStore.get_by_email(conn, "alice@example.com").user_id == u.user_id
    assert UserStore.get_by_id(conn, 9999) is None
    assert UserStore.get_by_email(conn, "nobody@example.com") is None


def test_mark_verified_sets_timestamp(conn):
    u = UserStore.find_or_create(conn, "c@example.com")
    assert u.email_verified_at is None
    UserStore.mark_verified(conn, u.user_id)
    refreshed = UserStore.get_by_id(conn, u.user_id)
    assert refreshed.email_verified_at is not None


def test_set_stripe_customer_id(conn):
    u = UserStore.find_or_create(conn, "d@example.com")
    UserStore.set_stripe_customer_id(conn, u.user_id, "cus_XYZ")
    refreshed = UserStore.get_by_id(conn, u.user_id)
    assert refreshed.stripe_customer_id == "cus_XYZ"
    assert UserStore.get_by_stripe_customer_id(conn, "cus_XYZ").user_id == u.user_id


def test_user_frozen():
    u = User(user_id=1, email="x@y.com", email_verified_at=None,
             stripe_customer_id=None, created_at="2026-04-22T00:00:00")
    with pytest.raises(Exception):
        u.email = "changed"


# ------------------------------------------------- AuthTokenStore


def test_mint_returns_raw_and_record(conn):
    raw, record = AuthTokenStore.mint(conn, "bob@example.com")
    assert raw != ""
    assert record.email == "bob@example.com"
    assert record.consumed_at is None


def test_mint_stores_hash_not_raw(conn):
    raw, record = AuthTokenStore.mint(conn, "bob@example.com")
    # The raw token never lands in storage verbatim.
    assert raw != record.token_hash
    assert record.token_hash == _hash_token(raw)


def test_consume_valid_token_returns_email(conn):
    raw, _ = AuthTokenStore.mint(conn, "bob@example.com")
    email = AuthTokenStore.consume(conn, raw)
    assert email == "bob@example.com"


def test_consume_twice_fails(conn):
    raw, _ = AuthTokenStore.mint(conn, "bob@example.com")
    assert AuthTokenStore.consume(conn, raw) == "bob@example.com"
    assert AuthTokenStore.consume(conn, raw) is None


def test_consume_wrong_token_returns_none(conn):
    AuthTokenStore.mint(conn, "bob@example.com")
    assert AuthTokenStore.consume(conn, "not-a-real-token") is None


def test_consume_expired_token_returns_none(conn):
    now = datetime(2026, 4, 22, 0, 0, 0)
    raw, _ = AuthTokenStore.mint(conn, "bob@example.com", expiry_minutes=5, now=now)
    later = now + timedelta(minutes=6)
    assert AuthTokenStore.consume(conn, raw, now=later) is None


def test_consume_empty_token_returns_none(conn):
    assert AuthTokenStore.consume(conn, "") is None


def test_mint_empty_email_raises(conn):
    with pytest.raises(ValueError, match="email required"):
        AuthTokenStore.mint(conn, "  ")


def test_purge_expired(conn):
    now = datetime(2026, 4, 22, 0, 0, 0)
    AuthTokenStore.mint(conn, "a@ex.com", expiry_minutes=5, now=now)
    AuthTokenStore.mint(conn, "b@ex.com", expiry_minutes=30, now=now)
    later = now + timedelta(minutes=10)
    n = AuthTokenStore.purge_expired(conn, now=later)
    assert n == 1


# ------------------------------------------------- SessionStore


def test_session_create_returns_token_and_expiry(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    now = datetime(2026, 4, 22, 0, 0, 0)
    s = SessionStore.create(conn, u.user_id, ttl_days=30, now=now)
    assert isinstance(s, Session)
    assert len(s.session_id) >= 32
    assert s.user_id == u.user_id
    assert s.expires_at > now.isoformat()
    assert s.revoked_at is None


def test_session_ttl_invalid(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    with pytest.raises(ValueError, match="ttl_days"):
        SessionStore.create(conn, u.user_id, ttl_days=0)


def test_session_get_active_happy(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    s = SessionStore.create(conn, u.user_id)
    assert SessionStore.get_active(conn, s.session_id).user_id == u.user_id


def test_session_get_active_expired_returns_none(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    now = datetime(2026, 4, 22, 0, 0, 0)
    s = SessionStore.create(conn, u.user_id, ttl_days=1, now=now)
    later = now + timedelta(days=2)
    assert SessionStore.get_active(conn, s.session_id, now=later) is None


def test_session_revoke(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    s = SessionStore.create(conn, u.user_id)
    SessionStore.revoke(conn, s.session_id)
    assert SessionStore.get_active(conn, s.session_id) is None
    # But the row is still there (audit trail)
    raw = SessionStore.get(conn, s.session_id)
    assert raw is not None
    assert raw.revoked_at is not None


def test_session_revoke_all_for_user(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    SessionStore.create(conn, u.user_id)
    SessionStore.create(conn, u.user_id)
    n = SessionStore.revoke_all_for_user(conn, u.user_id)
    assert n == 2


def test_session_get_unknown_returns_none(conn):
    assert SessionStore.get(conn, "nonexistent") is None
    assert SessionStore.get(conn, "") is None


def test_cookie_name_constant():
    assert COOKIE_NAME == "ee_session"


# ------------------------------------------------- SubscriptionStore


def test_subscription_upsert_creates(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    s = SubscriptionStore.upsert(
        conn, user_id=u.user_id,
        stripe_subscription_id="sub_ABC", status="active",
        current_period_end="2026-05-01T00:00:00",
    )
    assert isinstance(s, Subscription)
    assert s.status == "active"
    assert s.is_active()


def test_subscription_upsert_updates_existing(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, u.user_id, "sub_ABC", "active")
    SubscriptionStore.upsert(conn, u.user_id, "sub_ABC", "canceled")
    s = SubscriptionStore.get_by_stripe_id(conn, "sub_ABC")
    assert s.status == "canceled"
    assert s.is_active() is False


def test_subscription_has_active(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    assert SubscriptionStore.has_active(conn, u.user_id) is False
    SubscriptionStore.upsert(conn, u.user_id, "sub_1", "active")
    assert SubscriptionStore.has_active(conn, u.user_id) is True


def test_subscription_status_incomplete_not_active(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, u.user_id, "sub_X", "incomplete")
    assert SubscriptionStore.has_active(conn, u.user_id) is False


def test_subscription_trialing_counts_as_active(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, u.user_id, "sub_T", "trialing")
    assert SubscriptionStore.has_active(conn, u.user_id) is True


def test_subscription_list_for_user_sorted(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    SubscriptionStore.upsert(conn, u.user_id, "sub_1", "canceled", now=datetime(2026, 4, 1))
    SubscriptionStore.upsert(conn, u.user_id, "sub_2", "active", now=datetime(2026, 4, 20))
    subs = SubscriptionStore.list_for_user(conn, u.user_id)
    assert len(subs) == 2
    assert subs[0].stripe_subscription_id == "sub_2"  # newer updated_at first


def test_entitling_statuses_constant():
    assert set(ENTITLING_STATUSES) == {"active", "trialing"}


def test_subscription_cancel_at_period_end_persisted(conn):
    u = UserStore.find_or_create(conn, "bob@example.com")
    SubscriptionStore.upsert(
        conn, u.user_id, "sub_1", "active",
        cancel_at_period_end=True,
    )
    s = SubscriptionStore.get_by_stripe_id(conn, "sub_1")
    assert s.cancel_at_period_end is True
