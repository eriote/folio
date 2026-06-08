"""
SQLite layer for Folio.

All access to folio.db goes through this module.
Connection is opened once per process via get_conn().
"""

import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

from folio.paths import DB_PATH, ensure_dirs

_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    name_norm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS series (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    title_norm  TEXT NOT NULL,
    year        INTEGER,
    pages       INTEGER,
    description TEXT NOT NULL DEFAULT '',
    series_id   INTEGER REFERENCES series(id) ON DELETE SET NULL,
    series_num  TEXT NOT NULL DEFAULT '',
    epub_path   TEXT NOT NULL,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id   INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_id, author_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
    title, author, series,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS profiles (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS reading_log (
    id             INTEGER PRIMARY KEY,
    profile_id     INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    book_id        INTEGER REFERENCES books(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    author         TEXT NOT NULL,
    status         TEXT NOT NULL CHECK(status IN ('reading', 'read', 'want_to_read')),
    date_started   TEXT,
    date_finished  TEXT,
    added_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_title_norm  ON books(title_norm);
CREATE INDEX IF NOT EXISTS idx_authors_name_norm ON authors(name_norm);
CREATE INDEX IF NOT EXISTS idx_book_authors_book ON book_authors(book_id);
CREATE INDEX IF NOT EXISTS idx_reading_log_profile ON reading_log(profile_id, status);
"""


def normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        ensure_dirs()
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


# ── Authors ───────────────────────────────────────────────────────────────────

def get_or_create_author(name: str) -> int:
    conn = get_conn()
    norm = normalize(name)
    row = conn.execute("SELECT id FROM authors WHERE name_norm=?", (norm,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO authors (name, name_norm) VALUES (?,?)", (name, norm))
    conn.commit()
    return cur.lastrowid


def get_authors_for_book(book_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT a.id, a.name FROM authors a
        JOIN book_authors ba ON ba.author_id = a.id
        WHERE ba.book_id = ?
        ORDER BY ba.sort_order
    """, (book_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Series ────────────────────────────────────────────────────────────────────

def get_or_create_series(name: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM series WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


# ── Books ─────────────────────────────────────────────────────────────────────

def add_book(
    title: str,
    authors: list[str],
    epub_path: str,
    year: int | None = None,
    pages: int | None = None,
    description: str = "",
    series: str = "",
    series_num: str = "",
) -> int:
    conn = get_conn()
    series_id = get_or_create_series(series) if series else None
    now = datetime.now().isoformat(timespec="seconds")

    cur = conn.execute(
        """INSERT INTO books
           (title, title_norm, year, pages, description, series_id, series_num, epub_path, added_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (title, normalize(title), year, pages, description, series_id, series_num, epub_path, now),
    )
    book_id = cur.lastrowid

    for i, name in enumerate(authors):
        aid = get_or_create_author(name)
        conn.execute(
            "INSERT OR IGNORE INTO book_authors (book_id, author_id, sort_order) VALUES (?,?,?)",
            (book_id, aid, i),
        )

    # Update FTS index
    author_str = ", ".join(authors)
    conn.execute(
        "INSERT INTO books_fts (rowid, title, author, series) VALUES (?,?,?,?)",
        (book_id, title, author_str, series),
    )

    conn.commit()
    return book_id


def update_book(book_id: int, **fields) -> None:
    conn = get_conn()
    allowed = {"title", "year", "pages", "description", "series_num"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if "title" in updates:
        updates["title_norm"] = normalize(updates["title"])

    if "series" in fields:
        updates["series_id"] = get_or_create_series(fields["series"]) if fields["series"] else None

    if not updates:
        return

    cols = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE books SET {cols} WHERE id=?", (*updates.values(), book_id))
    conn.commit()


def delete_book(book_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.execute("DELETE FROM books_fts WHERE rowid=?", (book_id,))
    conn.commit()


def get_book(book_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("""
        SELECT b.*, s.name as series_name
        FROM books b
        LEFT JOIN series s ON s.id = b.series_id
        WHERE b.id=?
    """, (book_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["authors"] = get_authors_for_book(book_id)
    return result


def get_all_books(limit: int = 0, offset: int = 0) -> list[dict]:
    conn = get_conn()
    sql = """
        SELECT b.id, b.title, b.year, b.pages, b.series_num, b.added_at,
               s.name as series_name,
               GROUP_CONCAT(a.name, ', ') as author
        FROM books b
        LEFT JOIN series s ON s.id = b.series_id
        LEFT JOIN book_authors ba ON ba.book_id = b.id
        LEFT JOIN authors a ON a.id = ba.author_id
        GROUP BY b.id
        ORDER BY b.added_at DESC
    """
    if limit:
        sql += f" LIMIT {limit} OFFSET {offset}"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def search_books(query: str, limit: int = 50) -> list[dict]:
    conn = get_conn()
    # FTS search
    rows = conn.execute("""
        SELECT b.id, b.title, b.year, b.pages, b.series_num, b.added_at,
               s.name as series_name,
               GROUP_CONCAT(a.name, ', ') as author
        FROM books_fts f
        JOIN books b ON b.id = f.rowid
        LEFT JOIN series s ON s.id = b.series_id
        LEFT JOIN book_authors ba ON ba.book_id = b.id
        LEFT JOIN authors a ON a.id = ba.author_id
        WHERE books_fts MATCH ?
        GROUP BY b.id
        LIMIT ?
    """, (query + "*", limit)).fetchall()
    return [dict(r) for r in rows]


def count_books() -> int:
    return get_conn().execute("SELECT COUNT(*) FROM books").fetchone()[0]


def get_books_by_author(author_name: str) -> list[dict]:
    norm = normalize(author_name)
    rows = get_conn().execute("""
        SELECT b.id, b.title, b.year, b.pages, b.series_num, b.added_at,
               s.name as series_name,
               GROUP_CONCAT(a2.name, ', ') as author
        FROM authors a
        JOIN book_authors ba ON ba.author_id = a.id
        JOIN books b ON b.id = ba.book_id
        LEFT JOIN series s ON s.id = b.series_id
        LEFT JOIN book_authors ba2 ON ba2.book_id = b.id
        LEFT JOIN authors a2 ON a2.id = ba2.author_id
        WHERE a.name_norm = ?
        GROUP BY b.id
        ORDER BY s.name NULLS LAST, CAST(b.series_num AS REAL), b.year
    """, (norm,)).fetchall()
    return [dict(r) for r in rows]


def get_books_by_series(series_name: str) -> list[dict]:
    rows = get_conn().execute("""
        SELECT b.id, b.title, b.year, b.pages, b.series_num, b.added_at,
               s.name as series_name,
               GROUP_CONCAT(a.name, ', ') as author
        FROM series s
        JOIN books b ON b.series_id = s.id
        LEFT JOIN book_authors ba ON ba.book_id = b.id
        LEFT JOIN authors a ON a.id = ba.author_id
        WHERE s.name = ?
        GROUP BY b.id
        ORDER BY CAST(b.series_num AS REAL), b.title
    """, (series_name,)).fetchall()
    return [dict(r) for r in rows]


# ── Profiles ──────────────────────────────────────────────────────────────────

def get_or_create_default_profile() -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM profiles WHERE name='Principal'").fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO profiles (name) VALUES ('Principal')")
    conn.commit()
    return cur.lastrowid


# ── Reading log ───────────────────────────────────────────────────────────────

def get_reading_status(book_id: int, profile_id: int | None = None) -> str | None:
    """Returns 'reading', 'read', 'want_to_read' or None."""
    if profile_id is None:
        profile_id = get_or_create_default_profile()
    conn = get_conn()
    row = conn.execute(
        "SELECT status FROM reading_log WHERE book_id=? AND profile_id=? ORDER BY id DESC LIMIT 1",
        (book_id, profile_id),
    ).fetchone()
    return row["status"] if row else None


def set_reading_status(
    book_id: int,
    status: str,
    title: str,
    author: str,
    profile_id: int | None = None,
    date_started: str | None = None,
    date_finished: str | None = None,
) -> None:
    if profile_id is None:
        profile_id = get_or_create_default_profile()
    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT OR REPLACE INTO reading_log
           (profile_id, book_id, title, author, status, date_started, date_finished, added_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (profile_id, book_id, title, author, status,
         date_started or (now[:10] if status == "reading" else None),
         date_finished or (now[:10] if status == "read" else None),
         now),
    )
    conn.commit()


def remove_from_reading_log(book_id: int, profile_id: int | None = None) -> None:
    if profile_id is None:
        profile_id = get_or_create_default_profile()
    get_conn().execute(
        "DELETE FROM reading_log WHERE book_id=? AND profile_id=?",
        (book_id, profile_id),
    )
    get_conn().commit()


def get_reading_list(status: str, profile_id: int | None = None) -> list[dict]:
    if profile_id is None:
        profile_id = get_or_create_default_profile()
    rows = get_conn().execute("""
        SELECT rl.id, rl.book_id, rl.title, rl.author,
               rl.date_started, rl.date_finished, rl.added_at,
               b.pages, s.name as series_name, b.series_num
        FROM reading_log rl
        LEFT JOIN books b ON b.id = rl.book_id
        LEFT JOIN series s ON s.id = b.series_id
        WHERE rl.profile_id=? AND rl.status=?
        ORDER BY rl.added_at DESC
    """, (profile_id, status)).fetchall()
    return [dict(r) for r in rows]
