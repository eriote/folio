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
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Generator

import ebooklib
from ebooklib import epub
from PIL import Image
import io

from folio.database import add_book, get_conn
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
    series, num = "", ""
    for item in book.metadata.get("http://www.idpf.org/2007/opf", {}).get("meta", []):
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        attrs = item[1] if isinstance(item[1], dict) else {}
        if attrs.get("name") == "calibre:series":
            series = attrs.get("content", "").strip()
        elif attrs.get("name") == "calibre:series_index":
            num = attrs.get("content", "").strip()
    return series, num


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

def import_epub(epub_path: Path, action: str = "copy") -> int | None:
    """
    Import a single epub into the library.
    action: 'copy' | 'move' | 'reference'
    Returns the new book_id, or None if the file was already in the library.
    """
    ensure_dirs()
    conn = get_conn()
    meta = read_epub_metadata(epub_path)
    title = meta["title"]

    if action == "reference":
        dest = epub_path.resolve()
    else:
        rel = str(_dest_path(meta["authors"], title, epub_path))
        dest = EPUBS_DIR / rel

    dest_str = str(dest)
    src_str = str(epub_path.resolve())
    existing = {row[0] for row in conn.execute("SELECT epub_path FROM books").fetchall()}
    if dest_str in existing or src_str in existing:
        return None

    if action != "reference":
        dest.parent.mkdir(parents=True, exist_ok=True)
        if action == "move":
            shutil.move(str(epub_path), dest)
        else:
            shutil.copy2(epub_path, dest)

    book_id = add_book(
        title=title,
        authors=meta["authors"],
        epub_path=dest_str,
        year=meta["year"],
        pages=meta["pages"],
        description=meta["description"],
        series=meta["series"],
        series_num=meta["series_num"],
    )

    cover_src = dest if action != "reference" else epub_path
    try:
        book = epub.read_epub(str(cover_src), options={"ignore_ncx": True})
        _extract_cover(book, book_id)
    except Exception:
        pass

    return book_id


def find_epubs(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.suffix.lower() in EPUB_EXTENSIONS
    )


def scan_folder(
    root: Path,
    action: str = "copy",
) -> Generator[tuple[int, int, str], None, None]:
    """
    Scans root for epub files, imports each one into the Folio library.
    action: 'copy' | 'move' | 'reference'
    Yields (current, total, title) tuples so callers can show progress.
    """
    ensure_dirs()
    epubs = find_epubs(root)
    total = len(epubs)

    conn = get_conn()
    existing = {row[0] for row in conn.execute("SELECT epub_path FROM books").fetchall()}

    for i, epub_path in enumerate(epubs):
        meta = read_epub_metadata(epub_path)
        title = meta["title"]

        yield (i + 1, total, title)

        if action == "reference":
            dest = epub_path.resolve()
        else:
            rel = str(_dest_path(meta["authors"], title, epub_path))
            dest = EPUBS_DIR / rel

        dest_str = str(dest)
        src_str = str(epub_path.resolve())
        if dest_str in existing or src_str in existing:
            continue

        if action != "reference":
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                if action == "move":
                    shutil.move(str(epub_path), dest)
                else:
                    shutil.copy2(epub_path, dest)
            except Exception:
                continue

        book_id = add_book(
            title=title,
            authors=meta["authors"],
            epub_path=dest_str,
            year=meta["year"],
            pages=meta["pages"],
            description=meta["description"],
            series=meta["series"],
            series_num=meta["series_num"],
        )

        cover_src = dest if action != "reference" else epub_path
        try:
            book = epub.read_epub(str(cover_src), options={"ignore_ncx": True})
            _extract_cover(book, book_id)
        except Exception:
            pass


def embed_cover_in_epub(epub_path: Path, cover_bytes: bytes) -> bool:
    """
    Replace the cover image inside an epub file in-place.
    Returns True on success, False if the epub has no detectable cover slot.
    """
    img = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
    img = img.resize((600, 900), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    jpeg_bytes = buf.getvalue()

    tmp = epub_path.with_suffix(".folio_tmp.epub")
    try:
        with zipfile.ZipFile(epub_path, "r") as zin:
            names = set(zin.namelist())
            cover_item_path = _find_cover_path(zin, names)
            if not cover_item_path:
                return False

            with zipfile.ZipFile(tmp, "w") as zout:
                if "mimetype" in names:
                    zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"))
                for item in zin.infolist():
                    if item.filename == "mimetype":
                        continue
                    if item.filename == cover_item_path:
                        info = zipfile.ZipInfo(item.filename)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        zout.writestr(info, jpeg_bytes)
                    else:
                        zout.writestr(item, zin.read(item.filename))

        shutil.move(str(tmp), str(epub_path))
        return True
    except Exception:
        if tmp.exists():
            tmp.unlink()
        return False


def _find_cover_path(zin: zipfile.ZipFile, names: set) -> str | None:
    """Return the ZIP-internal path of the cover image, or None."""
    opf_path = None
    try:
        root = ET.fromstring(zin.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rf = root.find(".//c:rootfile", ns)
        if rf is not None:
            opf_path = rf.get("full-path")
    except Exception:
        pass

    if not opf_path or opf_path not in names:
        return None

    try:
        opf_root = ET.fromstring(zin.read(opf_path))
        opf_ns = "http://www.idpf.org/2007/opf"
        opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

        cover_id = None
        for meta in opf_root.iter(f"{{{opf_ns}}}meta"):
            if meta.get("name", "").lower() == "cover":
                cover_id = meta.get("content")
                break

        manifest = opf_root.find(f"{{{opf_ns}}}manifest")
        if manifest is None:
            return None

        if cover_id:
            for item in manifest:
                if item.get("id") == cover_id:
                    return (opf_dir + item.get("href", "")).lstrip("/")

        for item in manifest:
            if "cover-image" in item.get("properties", ""):
                return (opf_dir + item.get("href", "")).lstrip("/")

        for item in manifest:
            if "image" in item.get("media-type", ""):
                iid = item.get("id", "").lower()
                href = item.get("href", "")
                if "cover" in iid or "cover" in href.lower():
                    return (opf_dir + href).lstrip("/")
    except Exception:
        pass

    return None
