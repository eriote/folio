"""
Book detail page — shown when the user clicks a card in the grid.
"""

import threading
from datetime import date

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import (
    get_book, get_reading_status, set_reading_status, remove_from_reading_log
)
from folio.paths import COVERS_DIR

COVER_W, COVER_H = 300, 450

STATUS_LABELS = {
    "reading":       "Leyendo",
    "read":          "Leído ✓",
    "want_to_read":  "En lista",
}


def _load_cover(book_id: int) -> GdkPixbuf.Pixbuf | None:
    path = COVERS_DIR / f"{book_id}.webp"
    if path.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), COVER_W, COVER_H)
        except Exception:
            pass
    return None


def _placeholder() -> GdkPixbuf.Pixbuf:
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, COVER_W, COVER_H)
    pb.fill(0x2d2d2dff)
    return pb


class BookDetail(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=32)
        self.set_margin_top(32)
        self.set_margin_bottom(32)
        self.set_margin_start(40)
        self.set_margin_end(40)
        self._book = None
        self._build_ui()

    def _build_ui(self):
        # ── Left: cover ───────────────────────────────────────────────────
        self._cover = Gtk.Picture()
        self._cover.set_size_request(COVER_W, COVER_H)
        self._cover.set_content_fit(Gtk.ContentFit.COVER)
        self._cover.set_can_shrink(False)
        self._cover.set_valign(Gtk.Align.START)
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder()))
        self.append(self._cover)

        # ── Right: metadata + description ─────────────────────────────────
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_hexpand(True)
        right.set_valign(Gtk.Align.START)
        self.append(right)

        self._title_lbl = Gtk.Label()
        self._title_lbl.add_css_class("title-2")
        self._title_lbl.set_wrap(True)
        self._title_lbl.set_xalign(0)
        right.append(self._title_lbl)

        self._author_lbl = Gtk.Label()
        self._author_lbl.add_css_class("heading")
        self._author_lbl.set_xalign(0)
        self._author_lbl.set_margin_top(2)
        right.append(self._author_lbl)

        self._series_lbl = Gtk.Label()
        self._series_lbl.add_css_class("dim-label")
        self._series_lbl.set_xalign(0)
        self._series_lbl.set_visible(False)
        right.append(self._series_lbl)

        self._meta_lbl = Gtk.Label()
        self._meta_lbl.add_css_class("dim-label")
        self._meta_lbl.add_css_class("caption")
        self._meta_lbl.set_xalign(0)
        self._meta_lbl.set_margin_top(4)
        self._meta_lbl.set_margin_bottom(8)
        right.append(self._meta_lbl)

        # Action buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_bottom(8)

        self._read_btn = Gtk.Button(label="▶ Empezar a leer")
        self._read_btn.add_css_class("suggested-action")
        self._read_btn.add_css_class("pill")
        self._read_btn.connect("clicked", self._on_read_clicked)
        btn_row.append(self._read_btn)

        self._want_btn = Gtk.Button(label="+ Por leer")
        self._want_btn.add_css_class("pill")
        self._want_btn.connect("clicked", self._on_want_clicked)
        btn_row.append(self._want_btn)

        self._remove_btn = Gtk.Button(label="✕ Quitar de lista")
        self._remove_btn.add_css_class("pill")
        self._remove_btn.add_css_class("destructive-action")
        self._remove_btn.set_visible(False)
        self._remove_btn.connect("clicked", self._on_remove_clicked)
        btn_row.append(self._remove_btn)

        right.append(btn_row)

        self._status_lbl = Gtk.Label()
        self._status_lbl.add_css_class("dim-label")
        self._status_lbl.add_css_class("caption")
        self._status_lbl.set_xalign(0)
        self._status_lbl.set_visible(False)
        self._status_lbl.set_margin_bottom(8)
        right.append(self._status_lbl)

        # Description
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_max_content_height(300)
        scroll.set_propagate_natural_height(True)

        self._desc_lbl = Gtk.Label()
        self._desc_lbl.set_wrap(True)
        self._desc_lbl.set_xalign(0)
        self._desc_lbl.set_selectable(True)
        self._desc_lbl.set_margin_top(4)
        scroll.set_child(self._desc_lbl)
        right.append(scroll)

    # ── Public API ────────────────────────────────────────────────────────

    def load_book(self, book_id: int):
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder()))
        self._book = None

        def _bg():
            book   = get_book(book_id)
            pb     = _load_cover(book_id)
            status = get_reading_status(book_id)
            GLib.idle_add(self._populate, book, pb, status)

        threading.Thread(target=_bg, daemon=True).start()

    # ── Private ───────────────────────────────────────────────────────────

    def _populate(self, book: dict, pb, status: str | None):
        self._book = book

        if pb:
            self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(pb))

        self._title_lbl.set_label(book["title"])

        authors = [a["name"] for a in book.get("authors", [])]
        self._author_lbl.set_label(", ".join(authors) if authors else "")

        series = book.get("series_name") or ""
        series_num = book.get("series_num") or ""
        if series:
            label = f"Serie: {series}"
            if series_num:
                label += f"  ·  vol. {series_num}"
            self._series_lbl.set_label(label)
            self._series_lbl.set_visible(True)
        else:
            self._series_lbl.set_visible(False)

        parts = []
        if book.get("year"):
            parts.append(str(book["year"]))
        if book.get("pages"):
            parts.append(f"{book['pages']} páginas")
        self._meta_lbl.set_label("  ·  ".join(parts))

        self._desc_lbl.set_label(book.get("description") or "Sin descripción.")

        self._update_status_ui(status)

    def _update_status_ui(self, status: str | None):
        if status:
            self._status_lbl.set_label(f"Estado: {STATUS_LABELS.get(status, status)}")
            self._status_lbl.set_visible(True)
            self._remove_btn.set_visible(True)
            self._read_btn.set_sensitive(status != "reading")
            self._want_btn.set_sensitive(status != "want_to_read")
        else:
            self._status_lbl.set_visible(False)
            self._remove_btn.set_visible(False)
            self._read_btn.set_sensitive(True)
            self._want_btn.set_sensitive(True)

    def _author_str(self) -> str:
        if not self._book:
            return ""
        return ", ".join(a["name"] for a in self._book.get("authors", []))

    def _on_read_clicked(self, _):
        if not self._book:
            return
        set_reading_status(
            self._book["id"], "reading",
            self._book["title"], self._author_str(),
        )
        self._update_status_ui("reading")

    def _on_want_clicked(self, _):
        if not self._book:
            return
        set_reading_status(
            self._book["id"], "want_to_read",
            self._book["title"], self._author_str(),
        )
        self._update_status_ui("want_to_read")

    def _on_remove_clicked(self, _):
        if not self._book:
            return
        remove_from_reading_log(self._book["id"])
        self._update_status_ui(None)
