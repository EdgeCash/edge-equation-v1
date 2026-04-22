"""
Session management.

Sessions are opaque tokens (32 bytes urlsafe) stored in SQLite. A session
cookie ("ee_session") carries the token on every request; the API looks it
up in the sessions table and returns the current user.

Why opaque tokens and not JWT? Auditability. Every session is a row with a
revoked_at column -- we can kill a session the instant a device is lost
without worrying about token-in-the-wild. JWT revocation needs a blocklist
anyway, so the "stateless" benefit evaporates.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import secrets
import sqlite3
from typing import Any, Optional


DEFAULT_SESSION_TTL_DAYS = 30
COOKIE_NAME = "ee_session"


def _iso(ts: Any) -> str:
    if ts is None:
        return datetime.utcnow().isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: int
    created_at: str
    expires_at: str
    revoked_at: Optional[str]

    def is_active(self, now: Optional[Any] = None) -> bool:
        if self.revoked_at is not None:
            return False
        cur = _iso(now) if now is not None else datetime.utcnow().isoformat()
        return cur < self.expires_at

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
        }


class SessionStore:
    """
    Opaque-token session store:
    - create(conn, user_id, ttl_days, now)  -> Session
    - get(conn, session_id)                 -> Session | None
    - get_active(conn, session_id, now)     -> Session | None (revoked+expired filtered)
    - revoke(conn, session_id, now)         -> None
    - revoke_all_for_user(conn, user_id)    -> int
    """

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(
            session_id=row["session_id"],
            user_id=int(row["user_id"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )

    @staticmethod
    def create(
        conn: sqlite3.Connection,
        user_id: int,
        ttl_days: int = DEFAULT_SESSION_TTL_DAYS,
        now: Optional[Any] = None,
    ) -> Session:
        if ttl_days <= 0:
            raise ValueError(f"ttl_days must be positive, got {ttl_days}")
        now_dt = datetime.fromisoformat(_iso(now))
        expires = now_dt + timedelta(days=int(ttl_days))
        session_id = secrets.token_urlsafe(32)
        conn.execute(
            """
            INSERT INTO sessions (session_id, user_id, created_at, expires_at, revoked_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (session_id, int(user_id), now_dt.isoformat(), expires.isoformat()),
        )
        conn.commit()
        return Session(
            session_id=session_id,
            user_id=int(user_id),
            created_at=now_dt.isoformat(),
            expires_at=expires.isoformat(),
            revoked_at=None,
        )

    @staticmethod
    def get(conn: sqlite3.Connection, session_id: str) -> Optional[Session]:
        if not session_id:
            return None
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        return SessionStore._row_to_session(row) if row else None

    @staticmethod
    def get_active(
        conn: sqlite3.Connection,
        session_id: str,
        now: Optional[Any] = None,
    ) -> Optional[Session]:
        s = SessionStore.get(conn, session_id)
        if s is None or not s.is_active(now):
            return None
        return s

    @staticmethod
    def revoke(
        conn: sqlite3.Connection,
        session_id: str,
        now: Optional[Any] = None,
    ) -> None:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
            (_iso(now), session_id),
        )
        conn.commit()

    @staticmethod
    def revoke_all_for_user(
        conn: sqlite3.Connection,
        user_id: int,
        now: Optional[Any] = None,
    ) -> int:
        cur = conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_iso(now), int(user_id)),
        )
        conn.commit()
        return cur.rowcount or 0
