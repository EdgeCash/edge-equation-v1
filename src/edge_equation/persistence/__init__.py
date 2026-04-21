"""
SQLite persistence layer.

Six modules, all using only stdlib sqlite3 (no ORM, no Pydantic):
- db.py                  -- connection + deterministic schema migrations
- slate_store.py         -- write/read slate-generation records
- pick_store.py          -- write/read Pick rows
- odds_cache.py          -- TTL cache for raw odds-API payloads
- realization_store.py   -- actual game / market outcomes
- (engine/realization.py -- RealizationTracker coordinator, lives in engine/)

Decimal values are stored as strings, JSON blobs as TEXT, timestamps as ISO-8601
strings. All writes go through transactions; all reads are side-effect-free.
"""
