"""Tiny SQLite cache so we never pay for the same query twice."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent / ".cache.db"


def _conn():
    c = sqlite3.connect(CACHE_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS cache(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    return c


def _hash(ns: str, payload) -> str:
    blob = json.dumps([ns, payload], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def get(ns: str, payload, max_age_days: float | None = 30):
    """Return cached value or None. Default TTL 30 days."""
    key = _hash(ns, payload)
    with _conn() as c:
        row = c.execute(
            "SELECT value, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    value, created = row
    if max_age_days is not None and (time.time() - created) > max_age_days * 86400:
        return None
    return json.loads(value)


def put(ns: str, payload, value) -> None:
    key = _hash(ns, payload)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cache(key, value, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time()),
        )


def stats() -> dict:
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    return {"entries": n, "path": str(CACHE_PATH)}
