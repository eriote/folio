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

CREATE TABLE IF NOT EXISTS external_books (
    id            INTEGER PRIMARY KEY,
    profile_id    INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    author        TEXT NOT NULL DEFAULT '',
    year          INTEGER,
    pages         INTEGER,
    status        TEXT NOT NULL DEFAULT 'read' CHECK(status IN ('reading','read','want_to_read')),
    date_started  TEXT,
    date_finished TEXT,
    rating        INTEGER CHECK(rating BETWEEN 1 AND 5),
    notes         TEXT NOT NULL DEFAULT '',
    added_at      TEXT NOT NULL
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
        _migrate(_conn)
        _conn.commit()
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    from folio.paths import EPUBS_DIR
    rows = conn.execute(
        "SELECT id, epub_path FROM books WHERE epub_path NOT LIKE '/%'"
    ).fetchall()
    for row in rows:
        abs_path = str(EPUBS_DIR / row["epub_path"])
        conn.execute("UPDATE books SET epub_path=? WHERE id=?", (abs_path, row["id"]))


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


def _refresh_fts(conn, book_id: int) -> None:
    row = conn.execute(
        "SELECT b.title, s.name FROM books b LEFT JOIN series s ON s.id=b.series_id WHERE b.id=?",
        (book_id,),
    ).fetchone()
    if not row:
        return
    author_str = ", ".join(
        r["name"] for r in conn.execute(
            "SELECT a.name FROM authors a JOIN book_authors ba ON ba.author_id=a.id "
            "WHERE ba.book_id=? ORDER BY ba.sort_order", (book_id,)
        ).fetchall()
    )
    conn.execute("DELETE FROM books_fts WHERE rowid=?", (book_id,))
    conn.execute(
        "INSERT INTO books_fts (rowid, title, author, series) VALUES (?,?,?,?)",
        (book_id, row[0], author_str, row[1] or ""),
    )


def set_book_authors(book_id: int, author_names: list[str]) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM book_authors WHERE book_id=?", (book_id,))
    for i, name in enumerate(author_names):
        aid = get_or_create_author(name.strip())
        conn.execute(
            "INSERT OR IGNORE INTO book_authors (book_id, author_id, sort_order) VALUES (?,?,?)",
            (book_id, aid, i),
        )
    _refresh_fts(conn, book_id)
    conn.commit()


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
    _refresh_fts(conn, book_id)
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


_SORT_ORDER = {
    "recientes": "b.added_at DESC",
    "titulo":    "b.title_norm ASC",
    "autor":     "MIN(a.name_norm) NULLS LAST, b.title_norm ASC",
    "serie":     "s.name NULLS LAST, CAST(b.series_num AS REAL), b.title_norm ASC",
    "anyo":      "b.year DESC NULLS LAST, b.title_norm ASC",
}

_BOOKS_SELECT = """
    SELECT b.id, b.title, b.year, b.pages, b.series_num, b.added_at,
           s.name as series_name,
           GROUP_CONCAT(a.name, ', ') as author
    FROM books b
    LEFT JOIN series s ON s.id = b.series_id
    LEFT JOIN book_authors ba ON ba.book_id = b.id
    LEFT JOIN authors a ON a.id = ba.author_id
"""


def get_all_books(limit: int = 0, offset: int = 0, sort: str = "recientes") -> list[dict]:
    conn = get_conn()
    order = _SORT_ORDER.get(sort, _SORT_ORDER["recientes"])
    sql = _BOOKS_SELECT + f"GROUP BY b.id ORDER BY {order}"
    if limit:
        sql += f" LIMIT {limit} OFFSET {offset}"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def search_books(query: str, limit: int = 200, sort: str = "recientes") -> list[dict]:
    conn = get_conn()
    order = _SORT_ORDER.get(sort, _SORT_ORDER["recientes"])
    rows = conn.execute(
        _BOOKS_SELECT + """
        JOIN books_fts f ON f.rowid = b.id
        WHERE books_fts MATCH ?
        GROUP BY b.id
        ORDER BY """ + order + " LIMIT ?",
        (query + "*", limit),
    ).fetchall()
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


def get_profiles() -> list[dict]:
    get_or_create_default_profile()
    return [dict(r) for r in get_conn().execute(
        "SELECT id, name FROM profiles ORDER BY id"
    ).fetchall()]


def create_profile(name: str) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO profiles (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def rename_profile(profile_id: int, name: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE profiles SET name=? WHERE id=?", (name, profile_id))
    conn.commit()


def delete_profile(profile_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    conn.commit()


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


# ── External books ────────────────────────────────────────────────────────────

def add_external_book(
    profile_id: int,
    title: str,
    author: str = "",
    year: int | None = None,
    pages: int | None = None,
    status: str = "read",
    date_started: str | None = None,
    date_finished: str | None = None,
    rating: int | None = None,
    notes: str = "",
) -> int:
    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO external_books
           (profile_id, title, author, year, pages, status,
            date_started, date_finished, rating, notes, added_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (profile_id, title, author, year, pages, status,
         date_started, date_finished, rating, notes, now),
    )
    conn.commit()
    return cur.lastrowid


def get_external_books(status: str, profile_id: int | None = None) -> list[dict]:
    if profile_id is None:
        profile_id = get_or_create_default_profile()
    rows = get_conn().execute("""
        SELECT id, title, author, year, pages, status,
               date_started, date_finished, rating, notes, added_at
        FROM external_books
        WHERE profile_id=? AND status=?
        ORDER BY added_at DESC
    """, (profile_id, status)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["book_id"] = None
        d["is_external"] = True
        result.append(d)
    return result


def delete_external_book(ext_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM external_books WHERE id=?", (ext_id,))
    conn.commit()


def update_external_book(ext_id: int, **fields) -> None:
    allowed = {"title", "author", "year", "pages", "status",
               "date_started", "date_finished", "rating", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_conn()
    cols = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE external_books SET {cols} WHERE id=?",
                 (*updates.values(), ext_id))
    conn.commit()


# ── Reading statistics ────────────────────────────────────────────────────────

def get_reading_stats(profile_id: int | None = None, year: int | None = None) -> dict:
    if profile_id is None:
        profile_id = get_or_create_default_profile()
    conn = get_conn()

    yc_rl = f"AND strftime('%Y', rl.date_finished) = '{year}'" if year else ""
    yc_ext = f"AND strftime('%Y', date_finished) = '{year}'" if year else ""

    # Monthly books finished (1-based month string "01".."12")
    monthly: dict[str, int] = {}
    for r in conn.execute(f"""
        SELECT strftime('%m', date_finished) m, COUNT(*) c
        FROM reading_log
        WHERE profile_id=? AND status='read' AND date_finished IS NOT NULL {yc_rl}
        GROUP BY m
    """, (profile_id,)).fetchall():
        monthly[r[0]] = monthly.get(r[0], 0) + r[1]
    for r in conn.execute(f"""
        SELECT strftime('%m', date_finished) m, COUNT(*) c
        FROM external_books
        WHERE profile_id=? AND status='read' AND date_finished IS NOT NULL {yc_ext}
        GROUP BY m
    """, (profile_id,)).fetchall():
        monthly[r[0]] = monthly.get(r[0], 0) + r[1]

    # Total books read
    total_rl = conn.execute(f"""
        SELECT COUNT(*) FROM reading_log
        WHERE profile_id=? AND status='read' {yc_rl}
    """, (profile_id,)).fetchone()[0] or 0
    total_ext = conn.execute(f"""
        SELECT COUNT(*) FROM external_books
        WHERE profile_id=? AND status='read' {yc_ext}
    """, (profile_id,)).fetchone()[0] or 0
    total_books = total_rl + total_ext

    # Total pages read
    pages_rl = conn.execute(f"""
        SELECT COALESCE(SUM(b.pages), 0)
        FROM reading_log rl JOIN books b ON b.id=rl.book_id
        WHERE rl.profile_id=? AND rl.status='read' AND b.pages IS NOT NULL {yc_rl}
    """, (profile_id,)).fetchone()[0] or 0
    pages_ext = conn.execute(f"""
        SELECT COALESCE(SUM(pages), 0)
        FROM external_books
        WHERE profile_id=? AND status='read' AND pages IS NOT NULL {yc_ext}
    """, (profile_id,)).fetchone()[0] or 0
    total_pages = pages_rl + pages_ext

    # Top authors (combine reading_log + external_books)
    author_counts: dict[str, int] = {}
    for r in conn.execute(f"""
        SELECT author, COUNT(*) c FROM reading_log
        WHERE profile_id=? AND status='read' AND author!='' {yc_rl}
        GROUP BY author
    """, (profile_id,)).fetchall():
        author_counts[r[0]] = author_counts.get(r[0], 0) + r[1]
    for r in conn.execute(f"""
        SELECT author, COUNT(*) c FROM external_books
        WHERE profile_id=? AND status='read' AND author!='' {yc_ext}
        GROUP BY author
    """, (profile_id,)).fetchall():
        author_counts[r[0]] = author_counts.get(r[0], 0) + r[1]
    top_authors = sorted(author_counts.items(), key=lambda x: -x[1])[:10]

    # Available years (for the year filter dropdown)
    years: set[int] = set()
    for r in conn.execute(
        "SELECT DISTINCT strftime('%Y', date_finished) y FROM reading_log "
        "WHERE profile_id=? AND date_finished IS NOT NULL", (profile_id,)
    ).fetchall():
        if r[0]:
            years.add(int(r[0]))
    for r in conn.execute(
        "SELECT DISTINCT strftime('%Y', date_finished) y FROM external_books "
        "WHERE profile_id=? AND date_finished IS NOT NULL", (profile_id,)
    ).fetchall():
        if r[0]:
            years.add(int(r[0]))

    return {
        "total_books": total_books,
        "total_pages": total_pages,
        "unique_authors": len(author_counts),
        "monthly": monthly,
        "top_authors": top_authors,
        "years": sorted(years, reverse=True),
    }


# ── Discover / recommendations ────────────────────────────────────────────────

def _profile_id_safe(profile_id: int | None) -> int:
    return profile_id if profile_id is not None else get_or_create_default_profile()


def get_series_continuations(profile_id: int | None = None, limit: int = 20) -> list[dict]:
    """Next unread book per series where the user has read at least one book."""
    pid = _profile_id_safe(profile_id)
    conn = get_conn()
    rows = conn.execute("""
        WITH logged AS (
            SELECT book_id FROM reading_log
            WHERE profile_id=? AND book_id IS NOT NULL
        ),
        started_series AS (
            SELECT DISTINCT b.series_id
            FROM logged l JOIN books b ON b.id=l.book_id
            WHERE b.series_id IS NOT NULL
        )
        SELECT b.id, b.title, b.year, b.pages, b.series_num,
               s.name as series_name,
               GROUP_CONCAT(a.name, ', ') as author
        FROM books b
        JOIN series s ON s.id=b.series_id
        LEFT JOIN book_authors ba ON ba.book_id=b.id
        LEFT JOIN authors a ON a.id=ba.author_id
        WHERE b.series_id IN (SELECT series_id FROM started_series)
          AND b.id NOT IN (SELECT book_id FROM logged)
        GROUP BY b.id
        ORDER BY s.name, CAST(b.series_num AS REAL)
        LIMIT ?
    """, (pid, limit)).fetchall()
    return [dict(r) for r in rows]


def get_more_from_favorite_authors(profile_id: int | None = None, limit: int = 15) -> list[dict]:
    """Unread library books by authors the user has already read."""
    pid = _profile_id_safe(profile_id)
    conn = get_conn()
    rows = conn.execute("""
        WITH read_author_ids AS (
            SELECT DISTINCT ba.author_id
            FROM reading_log rl
            JOIN books b ON b.id=rl.book_id
            JOIN book_authors ba ON ba.book_id=b.id
            WHERE rl.profile_id=? AND rl.status IN ('reading','read')
        ),
        logged AS (
            SELECT book_id FROM reading_log WHERE profile_id=? AND book_id IS NOT NULL
        )
        SELECT b.id, b.title, b.year, b.pages, b.series_num,
               s.name as series_name,
               GROUP_CONCAT(a2.name, ', ') as author
        FROM books b
        JOIN book_authors ba ON ba.book_id=b.id
        JOIN read_author_ids ra ON ra.author_id=ba.author_id
        LEFT JOIN series s ON s.id=b.series_id
        LEFT JOIN book_authors ba2 ON ba2.book_id=b.id
        LEFT JOIN authors a2 ON a2.id=ba2.author_id
        WHERE b.id NOT IN (SELECT book_id FROM logged)
        GROUP BY b.id
        ORDER BY RANDOM()
        LIMIT ?
    """, (pid, pid, limit)).fetchall()
    return [dict(r) for r in rows]


def get_oldest_want_to_read(profile_id: int | None = None, limit: int = 8) -> list[dict]:
    """Books that have been on want_to_read the longest."""
    pid = _profile_id_safe(profile_id)
    rows = get_conn().execute("""
        SELECT rl.book_id, rl.title, rl.author, rl.added_at,
               b.pages, s.name as series_name, b.series_num
        FROM reading_log rl
        LEFT JOIN books b ON b.id=rl.book_id
        LEFT JOIN series s ON s.id=b.series_id
        WHERE rl.profile_id=? AND rl.status='want_to_read'
        ORDER BY rl.added_at ASC
        LIMIT ?
    """, (pid, limit)).fetchall()
    return [dict(r) for r in rows]


def get_random_unread(profile_id: int | None = None, limit: int = 5) -> list[dict]:
    """Random books from the library never touched by this profile."""
    pid = _profile_id_safe(profile_id)
    rows = get_conn().execute("""
        WITH logged AS (
            SELECT book_id FROM reading_log WHERE profile_id=? AND book_id IS NOT NULL
        )
        SELECT b.id, b.title, b.year, b.pages, b.series_num,
               s.name as series_name,
               GROUP_CONCAT(a.name, ', ') as author
        FROM books b
        LEFT JOIN series s ON s.id=b.series_id
        LEFT JOIN book_authors ba ON ba.book_id=b.id
        LEFT JOIN authors a ON a.id=ba.author_id
        WHERE b.id NOT IN (SELECT book_id FROM logged)
        GROUP BY b.id
        ORDER BY RANDOM()
        LIMIT ?
    """, (pid, limit)).fetchall()
    return [dict(r) for r in rows]
