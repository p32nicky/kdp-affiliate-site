import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

# Use Postgres on Vercel (DATABASE_URL set), SQLite locally
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(_DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


@dataclass
class Product:
    id: Optional[int]
    title: str
    slug: str
    url: str
    affiliate_url: str
    description: str
    image_url: str
    local_image_path: str
    first_seen_at: str
    last_seen_at: str
    is_active: int = 1


# ── connection helpers ──────────────────────────────────────────────────────

@contextmanager
def _pg_conn():
    conn = psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _sqlite_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _get_conn(db_path: str):
    """Return the right context manager based on environment."""
    if USE_POSTGRES:
        return _pg_conn()
    return _sqlite_conn(db_path)


# ── schema ──────────────────────────────────────────────────────────────────

_CREATE_SQL_SQLITE = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    affiliate_url TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    local_image_path TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_url ON products(url);
CREATE INDEX IF NOT EXISTS idx_first_seen ON products(first_seen_at);
"""

_CREATE_SQL_PG = """
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    affiliate_url TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    local_image_path TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_url ON products(url);
CREATE INDEX IF NOT EXISTS idx_first_seen ON products(first_seen_at);
"""


def init_db(db_path: str) -> None:
    if not USE_POSTGRES:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _get_conn(db_path) as conn:
        cur = conn.cursor() if USE_POSTGRES else conn
        sql = _CREATE_SQL_PG if USE_POSTGRES else _CREATE_SQL_SQLITE
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    if USE_POSTGRES:
                        cur.execute(stmt)
                    else:
                        conn.execute(stmt)
                except Exception:
                    pass  # index already exists etc.


# ── write operations ─────────────────────────────────────────────────────────

def upsert_products(db_path: str, items: list[dict]) -> int:
    inserted = 0
    with _get_conn(db_path) as conn:
        for item in items:
            if USE_POSTGRES:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO products
                        (title, slug, url, affiliate_url, description, image_url,
                         local_image_path, first_seen_at, last_seen_at, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                    ON CONFLICT (url) DO UPDATE
                        SET last_seen_at = EXCLUDED.last_seen_at, is_active = 1
                    RETURNING (xmax = 0) AS is_new
                """, (
                    item["title"], item["slug"], item["url"], item["affiliate_url"],
                    item["description"], item["image_url"], item.get("local_image_path", ""),
                    item["first_seen_at"], item["last_seen_at"],
                ))
                row = cur.fetchone()
                if row and row["is_new"]:
                    inserted += 1
            else:
                existing = conn.execute(
                    "SELECT id FROM products WHERE url = ?", (item["url"],)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE products SET last_seen_at=?, is_active=1 WHERE url=?",
                        (item["last_seen_at"], item["url"]),
                    )
                else:
                    conn.execute("""
                        INSERT INTO products
                            (title, slug, url, affiliate_url, description, image_url,
                             local_image_path, first_seen_at, last_seen_at, is_active)
                        VALUES (?,?,?,?,?,?,?,?,?,1)
                    """, (
                        item["title"], item["slug"], item["url"], item["affiliate_url"],
                        item["description"], item["image_url"], item.get("local_image_path", ""),
                        item["first_seen_at"], item["last_seen_at"],
                    ))
                    inserted += 1
    return inserted


def update_local_image(db_path: str, url: str, local_image_path: str) -> None:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        if USE_POSTGRES:
            conn.cursor().execute(
                f"UPDATE products SET local_image_path={ph} WHERE url={ph}",
                (local_image_path, url),
            )
        else:
            conn.execute(
                f"UPDATE products SET local_image_path={ph} WHERE url={ph}",
                (local_image_path, url),
            )


# ── read operations ──────────────────────────────────────────────────────────

def _rows(conn, sql: str, params: tuple = ()) -> list:
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    return conn.execute(sql, params).fetchall()


def _one(conn, sql: str, params: tuple = ()):
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()
    return conn.execute(sql, params).fetchone()


def list_products(
    db_path: str,
    query: str = "",
    page: int = 1,
    per_page: int = 24,
) -> tuple[list, int]:
    ph = "%s" if USE_POSTGRES else "?"
    offset = (page - 1) * per_page
    with _get_conn(db_path) as conn:
        if query:
            like = f"%{query}%"
            rows = _rows(conn,
                f"""SELECT * FROM products WHERE is_active=1
                    AND (title ILIKE {ph} OR description ILIKE {ph})
                    ORDER BY first_seen_at DESC LIMIT {ph} OFFSET {ph}""",
                (like, like, per_page, offset),
            ) if USE_POSTGRES else _rows(conn,
                f"""SELECT * FROM products WHERE is_active=1
                    AND (title LIKE {ph} OR description LIKE {ph})
                    ORDER BY first_seen_at DESC LIMIT {ph} OFFSET {ph}""",
                (like, like, per_page, offset),
            )
            total = (_one(conn,
                f"SELECT COUNT(*) FROM products WHERE is_active=1 AND (title ILIKE {ph} OR description ILIKE {ph})",
                (like, like),
            ) or {}).get("count", 0) if USE_POSTGRES else (
                _one(conn,
                    f"SELECT COUNT(*) FROM products WHERE is_active=1 AND (title LIKE {ph} OR description LIKE {ph})",
                    (like, like),
                ) or [0]
            )[0]
        else:
            rows = _rows(conn,
                f"SELECT * FROM products WHERE is_active=1 ORDER BY first_seen_at DESC LIMIT {ph} OFFSET {ph}",
                (per_page, offset),
            )
            total = (_one(conn, "SELECT COUNT(*) as count FROM products WHERE is_active=1") or {}).get("count", 0) \
                if USE_POSTGRES else \
                (_one(conn, "SELECT COUNT(*) FROM products WHERE is_active=1") or [0])[0]
    return rows, total


def get_latest_products(db_path: str, limit: int = 50) -> list:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        return _rows(conn,
            f"SELECT * FROM products WHERE is_active=1 ORDER BY first_seen_at DESC LIMIT {ph}",
            (limit,),
        )


def get_product_by_slug(db_path: str, slug: str) -> Optional[dict]:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        return _one(conn,
            f"SELECT * FROM products WHERE slug={ph} AND is_active=1", (slug,)
        )
