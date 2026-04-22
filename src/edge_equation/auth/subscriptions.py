"""
Subscription records.

One row per Stripe subscription (identified by stripe_subscription_id).
Upserted from the Stripe webhook handler; read by the paywall endpoints to
decide whether a user's /premium-edge request succeeds.

'status' mirrors Stripe's own subscription.status verbatim:
  active, trialing, past_due, canceled, unpaid, incomplete,
  incomplete_expired, paused.

Only 'active' and 'trialing' are considered entitling for our purposes.
"""
from dataclasses import dataclass
from datetime import datetime
import sqlite3
from typing import Any, List, Optional


ENTITLING_STATUSES = ("active", "trialing")


def _iso(ts: Any) -> str:
    if ts is None:
        return datetime.utcnow().isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


@dataclass(frozen=True)
class Subscription:
    subscription_id: int
    user_id: int
    stripe_subscription_id: str
    status: str
    current_period_end: Optional[str]
    cancel_at_period_end: bool
    created_at: str
    updated_at: str

    def is_active(self) -> bool:
        return self.status in ENTITLING_STATUSES

    def to_dict(self) -> dict:
        return {
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "stripe_subscription_id": self.stripe_subscription_id,
            "status": self.status,
            "current_period_end": self.current_period_end,
            "cancel_at_period_end": self.cancel_at_period_end,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SubscriptionStore:
    """
    Upsert-by-stripe-id store:
    - upsert(conn, user_id, stripe_subscription_id, status, current_period_end,
             cancel_at_period_end, now) -> Subscription
    - get_by_stripe_id(conn, stripe_id)  -> Subscription | None
    - list_for_user(conn, user_id)       -> list[Subscription]
    - has_active(conn, user_id)          -> bool
    """

    @staticmethod
    def _row_to_sub(row) -> Subscription:
        return Subscription(
            subscription_id=int(row["id"]),
            user_id=int(row["user_id"]),
            stripe_subscription_id=row["stripe_subscription_id"],
            status=row["status"],
            current_period_end=row["current_period_end"],
            cancel_at_period_end=bool(int(row["cancel_at_period_end"] or 0)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def upsert(
        conn: sqlite3.Connection,
        user_id: int,
        stripe_subscription_id: str,
        status: str,
        current_period_end: Optional[str] = None,
        cancel_at_period_end: bool = False,
        now: Optional[Any] = None,
    ) -> Subscription:
        ts = _iso(now)
        cancel_flag = 1 if cancel_at_period_end else 0
        conn.execute(
            """
            INSERT INTO subscriptions
                (user_id, stripe_subscription_id, status, current_period_end,
                 cancel_at_period_end, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stripe_subscription_id) DO UPDATE SET
                user_id = excluded.user_id,
                status = excluded.status,
                current_period_end = excluded.current_period_end,
                cancel_at_period_end = excluded.cancel_at_period_end,
                updated_at = excluded.updated_at
            """,
            (
                int(user_id), stripe_subscription_id, status,
                current_period_end, cancel_flag, ts, ts,
            ),
        )
        conn.commit()
        return SubscriptionStore.get_by_stripe_id(conn, stripe_subscription_id)

    @staticmethod
    def get_by_stripe_id(
        conn: sqlite3.Connection,
        stripe_subscription_id: str,
    ) -> Optional[Subscription]:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE stripe_subscription_id = ?",
            (stripe_subscription_id,),
        ).fetchone()
        return SubscriptionStore._row_to_sub(row) if row else None

    @staticmethod
    def list_for_user(
        conn: sqlite3.Connection,
        user_id: int,
    ) -> List[Subscription]:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY updated_at DESC",
            (int(user_id),),
        ).fetchall()
        return [SubscriptionStore._row_to_sub(r) for r in rows]

    @staticmethod
    def has_active(conn: sqlite3.Connection, user_id: int) -> bool:
        placeholders = ",".join("?" * len(ENTITLING_STATUSES))
        row = conn.execute(
            f"SELECT 1 FROM subscriptions WHERE user_id = ? AND status IN ({placeholders}) LIMIT 1",
            (int(user_id), *ENTITLING_STATUSES),
        ).fetchone()
        return row is not None
