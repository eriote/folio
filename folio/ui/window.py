"""
Main application window.
Shows a searchable grid of book covers.
"""

import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import get_all_books, search_books, count_books
from folio.paths import COVERS_DIR

CARD_W = 150
CARD_H = 225


def _load_cover_pixbuf(book_id: int) -> GdkPixbuf.Pixbuf | None:
    path = COVERS_DIR / f"{book_id}.webp"
    if path.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), CARD_W, CARD_H)
        except Exception:
            pass
    return None


def _placeholder_pixbuf() -> GdkPixbuf.Pixbuf:
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, CARD_W, CARD_H)
    pb.fill(0x2d2d2dff)
    return pb


class BookCard(Gtk.Box):
    def __init__(self, book: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._book = book
        self.set_size_request(CARD_W, -1)
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_bottom(8)

        self._cover = Gtk.Picture()
        self._cover.set_size_request(CARD_W, CARD_H)
        self._cover.set_content_fit(Gtk.ContentFit.COVER)
        self._cover.set_can_shrink(False)
        pb = _placeholder_pixbuf()
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        self.append(self._cover)

        title_lbl = Gtk.Label(label=book["title"])
        title_lbl.set_wrap(True)
        title_lbl.set_wrap_mode(2)  # WORD_CHAR
        title_lbl.set_max_width_chars(18)
        title_lbl.set_justify(Gtk.Justification.CENTER)
        title_lbl.add_css_class("caption")
        self.append(title_lbl)

        author = book.get("author") or ""
        if author:
            author_lbl = Gtk.Label(label=author)
            author_lbl.add_css_class("caption")
            author_lbl.add_css_class("dim-label")
            author_lbl.set_ellipsize(3)  # END
            author_lbl.set_max_width_chars(18)
            self.append(author_lbl)

    def load_cover_async(self):
        book_id = self._book["id"]
        def _bg():
            pb = _load_cover_pixbuf(book_id)
            if pb:
                GLib.idle_add(self._set_cover, pb)
        threading.Thread(target=_bg, daemon=True).start()

    def _set_cover(self, pb):
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(pb))


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Folio")
        self.set_default_size(1060, 660)
        self._build_ui()
        self._load_books()

    def _build_ui(self):
        # Header bar
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        self._search = Gtk.SearchEntry()
        self._search.set_hexpand(True)
        self._search.set_placeholder_text("Buscar libros, autores, series…")
        self._search.connect("search-changed", self._on_search_changed)
        header.set_title_widget(self._search)

        self._count_lbl = Gtk.Label()
        self._count_lbl.add_css_class("dim-label")
        self._count_lbl.add_css_class("caption")
        header.pack_end(self._count_lbl)

        # Scrollable book grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.set_child(scroll)

        self._flow = Gtk.FlowBox()
        self._flow.set_valign(Gtk.Align.START)
        self._flow.set_max_children_per_line(12)
        self._flow.set_min_children_per_line(2)
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_column_spacing(0)
        self._flow.set_row_spacing(0)
        self._flow.set_margin_top(16)
        self._flow.set_margin_start(16)
        self._flow.set_margin_end(16)
        scroll.set_child(self._flow)

    def _load_books(self, query: str = ""):
        while self._flow.get_first_child():
            self._flow.remove(self._flow.get_first_child())

        def _bg():
            if query.strip():
                books = search_books(query)
            else:
                books = get_all_books()
            GLib.idle_add(self._populate, books, query)

        threading.Thread(target=_bg, daemon=True).start()

    def _populate(self, books: list, query: str):
        total = count_books()
        if query:
            self._count_lbl.set_label(f"{len(books)} de {total} libros")
        else:
            self._count_lbl.set_label(f"{total} libros")

        for book in books:
            card = BookCard(book)
            self._flow.append(card)
            card.load_cover_async()

    def _on_search_changed(self, entry):
        self._load_books(entry.get_text())
