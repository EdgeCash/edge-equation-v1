"""
Magic-link auth tokens.

Flow:
  1. User types email -> AuthTokenStore.mint(email) -> (raw_token, record).
     The raw_token is sent via email. The DB stores only the SHA-256 hash.
  2. User clicks the link -> AuthTokenStore.consume(token) verifies the
     hash, checks expiry + unused, marks consumed, and returns the email.

Tokens are 32 bytes of secrets.token_urlsafe (256 bits) -- brute-force
infeasible. Short expiry (default 15 minutes) limits damage if email is
intercepted. Tokens are single-use; consuming twice fails.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import secrets
import sqlite3
from typing import Any, Optional, Tuple


DEFAULT_TOKEN_BYTES = 32
DEFAULT_EXPIRY_MINUTES = 15


def _iso(ts: Any) -> str:
    if ts is None:
        return datetime.utcnow().isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthTokenRecord:
    token_hash: str
    email: str
    created_at: str
    expires_at: str
    consumed_at: Optional[str]

    def to_dict(self) -> dict:
        return {
            "token_hash": self.token_hash,
            "email": self.email,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "consumed_at": self.consumed_at,
        }


class AuthTokenStore:
    """
    Single-use magic-link tokens:
    - mint(conn, email, expiry_minutes, now)       -> (raw_token, record)
    - consume(conn, raw_token, now)                -> email | None
    - purge_expired(conn, now)                     -> int
    - get_hash(conn, raw_token)                    -> record | None   (read-only)
    """

    @staticmethod
    def _now(ts: Optional[Any]) -> datetime:
        if ts is None:
            return datetime.utcnow()
        if isinstance(ts, datetime):
            return ts
        return datetime.fromisoformat(str(ts))

    @staticmethod
    def mint(
        conn: sqlite3.Connection,
        email: str,
        expiry_minutes: int = DEFAULT_EXPIRY_MINUTES,
        now: Optional[Any] = None,
        token_bytes: int = DEFAULT_TOKEN_BYTES,
    ) -> Tuple[str, AuthTokenRecord]:
        e = (email or "").strip().lower()
        if not e:
            raise ValueError("email required")
        created = AuthTokenStore._now(now)
        expires = created + timedelta(minutes=int(expiry_minutes))
        raw = secrets.token_urlsafe(token_bytes)
        token_hash = _hash_token(raw)
        conn.execute(
            """
            INSERT INTO auth_tokens (token_hash, email, created_at, expires_at, consumed_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (token_hash, e, created.isoformat(), expires.isoformat()),
        )
        conn.commit()
        record = AuthTokenRecord(
            token_hash=token_hash,
            email=e,
            created_at=created.isoformat(),
            expires_at=expires.isoformat(),
            consumed_at=None,
        )
        return raw, record

    @staticmethod
    def consume(
        conn: sqlite3.Connection,
        raw_token: str,
        now: Optional[Any] = None,
    ) -> Optional[str]:
        """Return the email if the token was valid + unused + unexpired."""
        if not raw_token:
            return None
        token_hash = _hash_token(raw_token)
        row = conn.execute(
            "SELECT * FROM auth_tokens WHERE token_hash = ?", (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["consumed_at"] is not None:
            return None
        now_dt = AuthTokenStore._now(now)
        if now_dt.isoformat() >= row["expires_at"]:
            return None
        conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? WHERE token_hash = ?",
            (now_dt.isoformat(), token_hash),
        )
        conn.commit()
        return row["email"]

    @staticmethod
    def purge_expired(
        conn: sqlite3.Connection,
        now: Optional[Any] = None,
    ) -> int:
        cur = conn.execute(
            "DELETE FROM auth_tokens WHERE expires_at <= ?",
            (_iso(now),),
        )
        conn.commit()
        return cur.rowcount or 0
