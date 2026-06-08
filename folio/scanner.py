"""
Scans a folder for epub files, extracts metadata, copies them into the
Folio library and inserts records into the database.

Usage:
    from folio.scanner import scan_folder
    for progress in scan_folder(Path("/home/user/Mis libros")):
        print(progress)  # (current, total, title)
"""

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Generator

import ebooklib
from ebooklib import epub
from PIL import Image
import io

from folio.database import add_book, get_conn, normalize
from folio.paths import COVERS_DIR, EPUBS_DIR, ensure_dirs

EPUB_EXTENSIONS = {".epub"}


# ── Metadata extraction ───────────────────────────────────────────────────────

def _get_opf_meta(book: epub.EpubBook, name: str) -> str:
    values = book.get_metadata("DC", name)
    if values:
        return str(values[0][0]).strip()
    return ""


def _fix_author(name: str) -> str:
    """'Rothfuss, Patrick' → 'Patrick Rothfuss'"""
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2:
            return f"{parts[1]} {parts[0]}"
    return name


def _extract_series(book: epub.EpubBook) -> tuple[str, str]:
    """Returns (series_name, series_num) from Calibre metadata if present."""
    for meta in book.get_metadata("OPF", "series"):
        name = meta[1].get("name") or meta[1].get("content") or ""
        if name:
            return name.strip(), ""
    # Calibre stores series in <meta name="calibre:series">
    for item in book.metadata.get("http://www.idpf.org/2007/opf", {}).get("meta", []):
        if isinstance(item, tuple) and len(item) == 2:
            attrs = item[1] if isinstance(item[1], dict) else {}
            if attrs.get("name") == "calibre:series":
                series = attrs.get("content", "").strip()
            if attrs.get("name") == "calibre:series_index":
                num = attrs.get("content", "").strip()
    try:
        return series, num
    except UnboundLocalError:
        return "", ""


def _estimate_pages(book: epub.EpubBook) -> int | None:
    """Rough page estimate: ~250 words per page."""
    total_words = 0
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        content = item.get_content().decode("utf-8", errors="ignore")
        text = re.sub(r"<[^>]+>", " ", content)
        total_words += len(text.split())
    if total_words == 0:
        return None
    return max(1, total_words // 250)


def _extract_cover(book: epub.EpubBook, book_id: int) -> None:
    """Saves cover as COVERS_DIR/{book_id}.webp (300×450 px)."""
    image_bytes = None

    # Method 1: <meta name="cover">
    cover_id = None
    for item in book.get_metadata("OPF", "meta"):
        if isinstance(item, tuple) and len(item) == 2:
            attrs = item[1] if isinstance(item[1], dict) else {}
            if attrs.get("name") == "cover":
                cover_id = attrs.get("content")
                break

    if cover_id:
        item = book.get_item_with_id(cover_id)
        if item:
            image_bytes = item.get_content()

    # Method 2: properties="cover-image"
    if not image_bytes:
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            if "cover" in (item.get_name() or "").lower():
                image_bytes = item.get_content()
                break

    if not image_bytes:
        return

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((300, 450), Image.LANCZOS)
        img.save(COVERS_DIR / f"{book_id}.webp", "WEBP", quality=85)
    except Exception:
        pass


def read_epub_metadata(epub_path: Path) -> dict:
    """
    Returns a dict with: title, authors, year, pages, description,
    series, series_num. Never raises — returns partial data on errors.
    """
    result = {
        "title": epub_path.stem,
        "authors": [],
        "year": None,
        "pages": None,
        "description": "",
        "series": "",
        "series_num": "",
    }
    try:
        book = epub.read_epub(str(epub_path), options={"ignore_ncx": True})

        title = _get_opf_meta(book, "title")
        if title:
            result["title"] = title

        raw_authors = book.get_metadata("DC", "creator")
        result["authors"] = [_fix_author(a[0]) for a in raw_authors if a[0].strip()]

        date = _get_opf_meta(book, "date")
        if date:
            m = re.search(r"\d{4}", date)
            if m:
                result["year"] = int(m.group())

        desc = _get_opf_meta(book, "description")
        if desc:
            result["description"] = re.sub(r"<[^>]+>", "", desc).strip()

        series, series_num = _extract_series(book)
        result["series"] = series
        result["series_num"] = series_num

        result["pages"] = _estimate_pages(book)

    except Exception:
        pass

    if not result["authors"]:
        result["authors"] = ["Desconocido"]

    return result


# ── Safe filename helpers ─────────────────────────────────────────────────────

def _safe(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)[:120].strip()


def _dest_path(authors: list[str], title: str, original: Path) -> Path:
    """
    Returns a relative path (from EPUBS_DIR) like:
        Rothfuss_Patrick/El_nombre_del_viento.epub
    """
    first_author = authors[0] if authors else "Desconocido"
    parts = first_author.split()
    if len(parts) >= 2:
        folder = _safe(f"{parts[-1]}_{' '.join(parts[:-1])}")
    else:
        folder = _safe(first_author)
    filename = _safe(title) + original.suffix.lower()
    return Path(folder) / filename


# ── Scanner ───────────────────────────────────────────────────────────────────

def find_epubs(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.suffix.lower() in EPUB_EXTENSIONS
    )


def scan_folder(
    root: Path,
    progress_cb=None,
) -> Generator[tuple[int, int, str], None, None]:
    """
    Scans root for epub files, imports each one into the Folio library.

    Yields (current, total, title) tuples so callers can show progress.
    Skips files already in the library (same relative epub_path).
    """
    ensure_dirs()
    epubs = find_epubs(root)
    total = len(epubs)

    # Build set of existing epub paths to skip duplicates
    conn = get_conn()
    existing = {
        row[0] for row in conn.execute("SELECT epub_path FROM books").fetchall()
    }

    for i, epub_path in enumerate(epubs):
        meta = read_epub_metadata(epub_path)
        title = meta["title"]

        yield (i + 1, total, title)

        rel = str(_dest_path(meta["authors"], title, epub_path))
        if rel in existing:
            continue

        dest = EPUBS_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(epub_path, dest)
        except Exception:
            continue

        book_id = add_book(
            title=title,
            authors=meta["authors"],
            epub_path=rel,
            year=meta["year"],
            pages=meta["pages"],
            description=meta["description"],
            series=meta["series"],
            series_num=meta["series_num"],
        )

        try:
            book = epub.read_epub(str(epub_path), options={"ignore_ncx": True})
            _extract_cover(book, book_id)
        except Exception:
            pass
