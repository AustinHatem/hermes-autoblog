"""Persistent keyword universe + topic queue.

One SQLite file (store.db) with three tables:
  - keywords: every kw we've seen, with DFS volume/competition/cpc if known
  - topics: titles queued for writing (UNIQUE on normalized title → exact dedupe)
  - topic_keywords: many-to-many mapping between topics and keywords
"""
import re
import sqlite3
import time
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "store.db"


def _conn():
    c = sqlite3.connect(STORE_PATH)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS keywords (
            keyword TEXT PRIMARY KEY,
            volume INTEGER,
            competition TEXT,
            cpc REAL,
            last_validated_at REAL
        );
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title_normalized TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            pillar TEXT,
            search_intent TEXT,
            rationale TEXT,
            score REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            output_path TEXT,
            created_at REAL NOT NULL,
            written_at REAL
        );
        CREATE TABLE IF NOT EXISTS topic_keywords (
            topic_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            PRIMARY KEY (topic_id, keyword),
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_topics_status_score
            ON topics(status, score DESC);
    """)
    return c


def _norm(title: str) -> str:
    """Normalize a title for exact-dedupe: lowercase, collapse whitespace, strip punct."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ──────────────────────────── keywords ───────────────────────────────

def upsert_keyword(keyword: str, volume: int | None = None,
                   competition: str | None = None, cpc: float | None = None) -> None:
    now = time.time() if volume is not None else None
    with _conn() as c:
        existing = c.execute("SELECT keyword FROM keywords WHERE keyword = ?",
                             (keyword,)).fetchone()
        if existing:
            if volume is not None:
                c.execute(
                    """UPDATE keywords SET volume=?, competition=?, cpc=?,
                       last_validated_at=? WHERE keyword=?""",
                    (volume, competition, cpc, now, keyword),
                )
        else:
            c.execute(
                """INSERT INTO keywords(keyword, volume, competition, cpc, last_validated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (keyword, volume, competition, cpc, now),
            )


def get_keywords_needing_validation(keywords: list[str],
                                    max_age_days: float = 30) -> list[str]:
    """Return keywords whose DFS data is missing or older than max_age_days."""
    cutoff = time.time() - max_age_days * 86400
    out: list[str] = []
    with _conn() as c:
        for kw in keywords:
            row = c.execute(
                "SELECT last_validated_at FROM keywords WHERE keyword = ?",
                (kw,),
            ).fetchone()
            if not row or row["last_validated_at"] is None or row["last_validated_at"] < cutoff:
                out.append(kw)
    return out


def get_keyword(keyword: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM keywords WHERE keyword = ?",
                        (keyword,)).fetchone()
    return dict(row) if row else None


# ──────────────────────────── topics ─────────────────────────────────

def add_topic(title: str, keywords: list[str], pillar: str | None = None,
              search_intent: str | None = None, rationale: str | None = None,
              score: float = 0.0) -> tuple[int | None, bool]:
    """Insert a topic. Returns (topic_id, is_new). If the normalized title
    already exists, returns (existing_id, False) — no overwrite."""
    norm = _norm(title)
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM topics WHERE title_normalized = ?", (norm,)
        ).fetchone()
        if existing:
            return existing["id"], False
        cur = c.execute(
            """INSERT INTO topics(title_normalized, title, pillar, search_intent,
                                  rationale, score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (norm, title, pillar, search_intent, rationale, score, time.time()),
        )
        tid = cur.lastrowid
        for i, kw in enumerate(keywords):
            c.execute(
                """INSERT OR IGNORE INTO topic_keywords(topic_id, keyword, is_primary)
                   VALUES (?, ?, ?)""",
                (tid, kw, 1 if i == 0 else 0),
            )
        return tid, True


def next_queued(topic_id: int | None = None) -> dict | None:
    """Get the next queued topic (highest score first) or a specific one by id."""
    with _conn() as c:
        if topic_id is not None:
            row = c.execute(
                "SELECT * FROM topics WHERE id = ? AND status = 'queued'",
                (topic_id,),
            ).fetchone()
        else:
            row = c.execute(
                """SELECT * FROM topics WHERE status = 'queued'
                   ORDER BY score DESC, created_at ASC LIMIT 1"""
            ).fetchone()
        if not row:
            return None
        topic = dict(row)
        kws = c.execute(
            """SELECT keyword, is_primary FROM topic_keywords
               WHERE topic_id = ? ORDER BY is_primary DESC, keyword""",
            (topic["id"],),
        ).fetchall()
        topic["keywords"] = [k["keyword"] for k in kws]
        return topic


def mark_written(topic_id: int, output_path: str) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE topics SET status='written', written_at=?, output_path=?
               WHERE id=?""",
            (time.time(), output_path, topic_id),
        )


def list_topics(status: str | None = None, limit: int = 50) -> list[dict]:
    with _conn() as c:
        if status:
            rows = c.execute(
                """SELECT * FROM topics WHERE status = ?
                   ORDER BY score DESC, created_at ASC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT * FROM topics
                   ORDER BY status, score DESC, created_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            kws = c.execute(
                """SELECT keyword FROM topic_keywords
                   WHERE topic_id = ? ORDER BY is_primary DESC, keyword""",
                (d["id"],),
            ).fetchall()
            d["keywords"] = [k["keyword"] for k in kws]
            out.append(d)
        return out


def stats() -> dict:
    with _conn() as c:
        kw = c.execute("SELECT COUNT(*) AS n FROM keywords").fetchone()["n"]
        kw_validated = c.execute(
            "SELECT COUNT(*) AS n FROM keywords WHERE volume IS NOT NULL"
        ).fetchone()["n"]
        q = c.execute(
            "SELECT status, COUNT(*) AS n FROM topics GROUP BY status"
        ).fetchall()
    return {
        "keywords_total": kw,
        "keywords_validated": kw_validated,
        "topics_by_status": {r["status"]: r["n"] for r in q},
        "path": str(STORE_PATH),
    }
