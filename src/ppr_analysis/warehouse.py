"""SQLite warehouse for PPR rows, Daft listings, and match results."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ppr_sales (
    ppr_id TEXT PRIMARY KEY,
    sale_date TEXT NOT NULL,
    address TEXT NOT NULL,
    eircode TEXT,
    county TEXT NOT NULL,
    price REAL NOT NULL,
    not_full_market_price INTEGER NOT NULL,
    vat_exclusive INTEGER NOT NULL,
    description TEXT,
    size_band TEXT
);

CREATE TABLE IF NOT EXISTS daft_listings (
    listing_id TEXT PRIMARY KEY,
    url TEXT,
    address TEXT NOT NULL,
    sold_date TEXT,
    sold_price REAL,
    asking_price REAL,
    beds INTEGER,
    baths INTEGER,
    property_type TEXT,
    floor_area_m2 REAL,
    ber TEXT,
    agent TEXT,
    source TEXT NOT NULL DEFAULT 'bulk'
);

CREATE TABLE IF NOT EXISTS matches (
    ppr_id TEXT PRIMARY KEY,
    listing_id TEXT,
    match_status TEXT NOT NULL,
    match_score REAL,
    daft_url TEXT,
    FOREIGN KEY (ppr_id) REFERENCES ppr_sales(ppr_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS http_cache (
    cache_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query_key TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    lat REAL,
    lng REAL,
    display_name TEXT,
    in_bounds INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    daft_cols = {row[1] for row in conn.execute("PRAGMA table_info(daft_listings)")}
    if "source" not in daft_cols:
        conn.execute("ALTER TABLE daft_listings ADD COLUMN source TEXT NOT NULL DEFAULT 'bulk'")
        conn.commit()


def prune_orphan_matches(conn: sqlite3.Connection) -> int:
    """Reset matches whose listing was removed so exports do not keep exact/high with empty Daft attrs."""
    cursor = conn.execute(
        """
        UPDATE matches
        SET listing_id = NULL,
            match_status = 'unmatched',
            match_score = 0,
            daft_url = NULL
        WHERE listing_id IS NOT NULL
          AND listing_id NOT IN (SELECT listing_id FROM daft_listings)
        """
    )
    return cursor.rowcount


def replace_bulk_daft_listings(conn: sqlite3.Connection, rows: list[dict], columns: list[str]) -> int:
    """Replace bulk crawl rows, keep fallback-search listings, then drop orphan matches."""
    bulk_ids = [row["listing_id"] for row in rows]
    conn.execute("DELETE FROM daft_listings WHERE COALESCE(source, 'bulk') != 'fallback'")
    if bulk_ids:
        placeholders = ", ".join("?" * len(bulk_ids))
        conn.execute(
            f"DELETE FROM daft_listings WHERE listing_id IN ({placeholders})",
            bulk_ids,
        )
    if rows:
        insert_cols = columns + ["source"]
        col_sql = ", ".join(insert_cols)
        placeholders = ", ".join("?" * len(insert_cols))
        payload = [tuple(row.get(col) for col in columns) + ("bulk",) for row in rows]
        conn.executemany(f"INSERT INTO daft_listings ({col_sql}) VALUES ({placeholders})", payload)
    prune_orphan_matches(conn)
    conn.commit()
    return len(payload) if rows else 0


def replace_table(conn: sqlite3.Connection, table: str, rows: list[dict], columns: list[str]) -> int:
    conn.execute(f"DELETE FROM {table}")
    if not rows:
        conn.commit()
        return 0
    placeholders = ", ".join("?" * len(columns))
    col_sql = ", ".join(columns)
    payload = [tuple(row.get(col) for col in columns) for row in rows]
    conn.executemany(f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})", payload)
    conn.commit()
    return len(payload)


def upsert_http_cache(conn: sqlite3.Connection, cache_key: str, url: str, fetched_at: str, body: str) -> None:
    conn.execute(
        """
        INSERT INTO http_cache (cache_key, url, fetched_at, body)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            url = excluded.url,
            fetched_at = excluded.fetched_at,
            body = excluded.body
        """,
        (cache_key, url, fetched_at, body),
    )
    conn.commit()


def get_http_cache(conn: sqlite3.Connection, cache_key: str) -> str | None:
    row = conn.execute("SELECT body FROM http_cache WHERE cache_key = ?", (cache_key,)).fetchone()
    return None if row is None else row["body"]
