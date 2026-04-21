"""
Odds cache with TTL.

Keys are opaque strings supplied by the caller (e.g. "theoddsapi:MLB:h2h:us").
Payloads are arbitrary JSON-serializable dicts.

API:
- put(conn, key, payload, ttl_seconds, now=None) -> None
- get(conn, key, now=None)                       -> dict or None (None if missing or expired)
- purge_expired(conn, now=None)                  -> int (rows deleted)
- stats(conn, now=None)                          -> dict (counts)

Expiration is compared in isoformat() lexicographic order (UTC naive). The
caller is responsible for consistent timezone usage; passing `now` explicitly
makes the cache deterministic for tests.
"""
from datetime import datetime, timedelta
import json
import sqlite3
from typing import Any, Dict, Optional


class OddsCache:
    """
    TTL-based cache for raw odds-API payloads:
    - put:            upsert a keyed payload with expiration = now + ttl
    - get:            return payload if still fresh; None if missing/expired
    - purge_expired:  delete all rows with expires_at <= now
    - stats:          {"total": N, "fresh": n1, "expired": n2}
    """

    @staticmethod
    def _now(now: Optional[datetime]) -> datetime:
        return now if now is not None else datetime.utcnow()

    @staticmethod
    def put(
        conn: sqlite3.Connection,
        key: str,
        payload: Dict[str, Any],
        ttl_seconds: int,
        now: Optional[datetime] = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        current = OddsCache._now(now)
        expires = current + timedelta(seconds=int(ttl_seconds))
        conn.execute(
            """
            INSERT INTO odds_cache (cache_key, payload_json, fetched_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (key, json.dumps(payload), current.isoformat(), expires.isoformat()),
        )
        conn.commit()

    @staticmethod
    def get(
        conn: sqlite3.Connection,
        key: str,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        current = OddsCache._now(now)
        row = conn.execute(
            "SELECT payload_json, expires_at FROM odds_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= current.isoformat():
            return None
        return json.loads(row["payload_json"])

    @staticmethod
    def purge_expired(
        conn: sqlite3.Connection,
        now: Optional[datetime] = None,
    ) -> int:
        current = OddsCache._now(now)
        cur = conn.execute(
            "DELETE FROM odds_cache WHERE expires_at <= ?",
            (current.isoformat(),),
        )
        conn.commit()
        return cur.rowcount

    @staticmethod
    def stats(
        conn: sqlite3.Connection,
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        current = OddsCache._now(now).isoformat()
        total = conn.execute("SELECT COUNT(*) AS c FROM odds_cache").fetchone()["c"]
        fresh = conn.execute(
            "SELECT COUNT(*) AS c FROM odds_cache WHERE expires_at > ?",
            (current,),
        ).fetchone()["c"]
        return {"total": int(total), "fresh": int(fresh), "expired": int(total - fresh)}
