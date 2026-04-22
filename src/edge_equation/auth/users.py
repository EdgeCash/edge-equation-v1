"""
User records.

One row per subscriber. Email is the natural key (UNIQUE constraint) so the
same magic-link sign-in always lands on the same user row. Stripe customer
ids are attached lazily the first time a user hits checkout.
"""
from dataclasses import dataclass
from datetime import datetime
import sqlite3
from typing import Any, Optional


def _iso(ts: Any) -> str:
    if ts is None:
        return datetime.utcnow().isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


@dataclass(frozen=True)
class User:
    user_id: int
    email: str
    email_verified_at: Optional[str]
    stripe_customer_id: Optional[str]
    created_at: str

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "email_verified_at": self.email_verified_at,
            "stripe_customer_id": self.stripe_customer_id,
            "created_at": self.created_at,
        }


class UserStore:
    """
    CRUD for users:
    - find_or_create(conn, email, now=None)         -> User (idempotent on email)
    - mark_verified(conn, user_id, now=None)        -> None
    - set_stripe_customer_id(conn, user_id, cid)    -> None
    - get_by_id(conn, user_id)                      -> User | None
    - get_by_email(conn, email)                     -> User | None
    - get_by_stripe_customer_id(conn, cid)          -> User | None
    """

    @staticmethod
    def _row_to_user(row) -> User:
        return User(
            user_id=int(row["id"]),
            email=row["email"],
            email_verified_at=row["email_verified_at"],
            stripe_customer_id=row["stripe_customer_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _normalize(email: str) -> str:
        return (email or "").strip().lower()

    @staticmethod
    def find_or_create(
        conn: sqlite3.Connection,
        email: str,
        now: Optional[Any] = None,
    ) -> User:
        e = UserStore._normalize(email)
        if not e:
            raise ValueError("email required")
        existing = UserStore.get_by_email(conn, e)
        if existing is not None:
            return existing
        conn.execute(
            "INSERT INTO users (email, email_verified_at, stripe_customer_id, created_at) "
            "VALUES (?, NULL, NULL, ?)",
            (e, _iso(now)),
        )
        conn.commit()
        return UserStore.get_by_email(conn, e)

    @staticmethod
    def mark_verified(
        conn: sqlite3.Connection,
        user_id: int,
        now: Optional[Any] = None,
    ) -> None:
        conn.execute(
            "UPDATE users SET email_verified_at = ? WHERE id = ?",
            (_iso(now), int(user_id)),
        )
        conn.commit()

    @staticmethod
    def set_stripe_customer_id(
        conn: sqlite3.Connection,
        user_id: int,
        stripe_customer_id: str,
    ) -> None:
        conn.execute(
            "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
            (stripe_customer_id, int(user_id)),
        )
        conn.commit()

    @staticmethod
    def get_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[User]:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return UserStore._row_to_user(row) if row else None

    @staticmethod
    def get_by_email(conn: sqlite3.Connection, email: str) -> Optional[User]:
        e = UserStore._normalize(email)
        row = conn.execute("SELECT * FROM users WHERE email = ?", (e,)).fetchone()
        return UserStore._row_to_user(row) if row else None

    @staticmethod
    def get_by_stripe_customer_id(
        conn: sqlite3.Connection,
        stripe_customer_id: str,
    ) -> Optional[User]:
        row = conn.execute(
            "SELECT * FROM users WHERE stripe_customer_id = ?",
            (stripe_customer_id,),
        ).fetchone()
        return UserStore._row_to_user(row) if row else None
