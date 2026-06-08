"""
Main application window.
Shows a searchable grid of book covers with navigation to book detail.
"""

import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import get_all_books, search_books, count_books, get_books_by_author, get_books_by_series
from folio.paths import COVERS_DIR
from folio.ui.book_detail import BookDetail
from folio.ui.reading import ReadingPage

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
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder_pixbuf()))
        self.append(self._cover)

        title_lbl = Gtk.Label(label=book["title"])
        title_lbl.set_wrap(True)
        title_lbl.set_wrap_mode(2)
        title_lbl.set_max_width_chars(18)
        title_lbl.set_justify(Gtk.Justification.CENTER)
        title_lbl.add_css_class("caption")
        self.append(title_lbl)

        author = book.get("author") or ""
        if author:
            author_lbl = Gtk.Label(label=author)
            author_lbl.add_css_class("caption")
            author_lbl.add_css_class("dim-label")
            author_lbl.set_ellipsize(3)
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

    @property
    def book(self):
        return self._book


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Folio")
        self.set_default_size(1060, 660)
        self._build_ui()
        self._load_books()

    def _build_ui(self):
        # ── Header bar ────────────────────────────────────────────────────
        self._header = Gtk.HeaderBar()
        self.set_titlebar(self._header)

        self._back_btn = Gtk.Button()
        self._back_btn.set_icon_name("go-previous-symbolic")
        self._back_btn.set_tooltip_text("Volver")
        self._back_btn.connect("clicked", self._on_back)
        self._back_btn.set_visible(False)
        self._header.pack_start(self._back_btn)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Buscar…")
        self._search.connect("search-changed", self._on_search_changed)
        self._header.pack_start(self._search)

        self._count_lbl = Gtk.Label()
        self._count_lbl.add_css_class("dim-label")
        self._count_lbl.add_css_class("caption")
        self._header.pack_end(self._count_lbl)

        # Tab switcher
        switcher_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        switcher_box.add_css_class("linked")

        self._tab_library = Gtk.ToggleButton(label="Biblioteca")
        self._tab_reading = Gtk.ToggleButton(label="Lecturas")
        self._tab_library.set_active(True)
        self._tab_library.connect("toggled", self._on_main_tab, "library")
        self._tab_reading.connect("toggled", self._on_main_tab, "reading")
        switcher_box.append(self._tab_library)
        switcher_box.append(self._tab_reading)
        self._header.set_title_widget(switcher_box)

        # ── Stack: grid page / detail page ────────────────────────────────
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(200)
        self.set_child(self._stack)

        # Grid page
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

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
        self._flow.connect("child-activated", self._on_card_activated)
        scroll.set_child(self._flow)
        self._stack.add_named(scroll, "grid")

        # Detail page (scrollable)
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_vexpand(True)
        self._detail = BookDetail(
            on_open_author=self._open_author_page,
            on_open_series=self._open_series_page,
        )
        detail_scroll.set_child(self._detail)
        self._stack.add_named(detail_scroll, "detail")

        # Reading page
        self._reading_page = ReadingPage(on_open_book=self._open_book_detail)
        self._stack.add_named(self._reading_page, "reading")

        # Collection page (author or series filtered grid)
        coll_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._coll_title = Gtk.Label()
        self._coll_title.add_css_class("title-4")
        self._coll_title.set_margin_top(16)
        self._coll_title.set_margin_bottom(4)
        self._coll_title.set_margin_start(20)
        self._coll_title.set_xalign(0)
        coll_outer.append(self._coll_title)

        coll_scroll = Gtk.ScrolledWindow()
        coll_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        coll_scroll.set_vexpand(True)
        self._coll_flow = Gtk.FlowBox()
        self._coll_flow.set_valign(Gtk.Align.START)
        self._coll_flow.set_max_children_per_line(12)
        self._coll_flow.set_min_children_per_line(2)
        self._coll_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._coll_flow.set_margin_top(8)
        self._coll_flow.set_margin_start(16)
        self._coll_flow.set_margin_end(16)
        self._coll_flow.connect("child-activated", self._on_coll_card_activated)
        coll_scroll.set_child(self._coll_flow)
        coll_outer.append(coll_scroll)
        self._stack.add_named(coll_outer, "collection")

    def _load_books(self, query: str = ""):
        while self._flow.get_first_child():
            self._flow.remove(self._flow.get_first_child())

        def _bg():
            books = search_books(query) if query.strip() else get_all_books()
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

    def _on_card_activated(self, flowbox, child):
        card = child.get_child()
        if not isinstance(card, BookCard):
            return
        self._open_book_detail(card.book["id"])

    def _open_book_detail(self, book_id: int):
        self._detail_came_from = self._stack.get_visible_child_name()
        self._detail.load_book(book_id)
        self._stack.set_visible_child_name("detail")
        self._back_btn.set_visible(True)
        self._tab_library.set_visible(False)
        self._tab_reading.set_visible(False)
        self._count_lbl.set_visible(False)

    def _on_back(self, _):
        current = self._stack.get_visible_child_name()
        if current == "detail":
            # From detail: go back to collection if we came from there, else grid/reading
            prev = getattr(self, "_detail_came_from", "grid")
            self._stack.set_visible_child_name(prev)
            if prev in ("grid", "reading"):
                self._back_btn.set_visible(False)
                self._tab_library.set_visible(True)
                self._tab_reading.set_visible(True)
                self._count_lbl.set_visible(prev == "grid")
                if prev == "reading":
                    self._reading_page.refresh()
            # if prev == "collection", back btn stays visible
        else:
            # From collection: back to grid or reading
            prev = "reading" if self._tab_reading.get_active() else "grid"
            self._stack.set_visible_child_name(prev)
            self._back_btn.set_visible(False)
            self._tab_library.set_visible(True)
            self._tab_reading.set_visible(True)
            self._count_lbl.set_visible(prev == "grid")

    def _on_main_tab(self, btn, key):
        if not btn.get_active():
            return
        if key == "library":
            self._tab_reading.set_active(False)
            self._search.set_visible(True)
            self._count_lbl.set_visible(True)
            self._stack.set_visible_child_name("grid")
        else:
            self._tab_library.set_active(False)
            self._search.set_visible(False)
            self._count_lbl.set_visible(False)
            self._stack.set_visible_child_name("reading")
            self._reading_page.refresh()

    def _open_author_page(self, author_name: str):
        self._coll_title.set_label(f"Autor: {author_name}")
        self._load_collection(get_books_by_author, author_name)

    def _open_series_page(self, series_name: str):
        self._coll_title.set_label(f"Serie: {series_name}")
        self._load_collection(get_books_by_series, series_name)

    def _load_collection(self, query_fn, arg: str):
        while self._coll_flow.get_first_child():
            self._coll_flow.remove(self._coll_flow.get_first_child())

        def _bg():
            books = query_fn(arg)
            GLib.idle_add(self._populate_collection, books)

        threading.Thread(target=_bg, daemon=True).start()
        self._stack.set_visible_child_name("collection")
        self._back_btn.set_visible(True)
        self._tab_library.set_visible(False)
        self._tab_reading.set_visible(False)
        self._count_lbl.set_visible(False)

    def _populate_collection(self, books: list):
        for book in books:
            card = BookCard(book)
            self._coll_flow.append(card)
            card.load_cover_async()
        n = len(books)
        self._coll_title.set_label(
            self._coll_title.get_label() + f"  ·  {n} libro{'s' if n != 1 else ''}"
        )

    def _on_coll_card_activated(self, flowbox, child):
        card = child.get_child()
        if isinstance(card, BookCard):
            self._open_book_detail(card.book["id"])

    def _on_search_changed(self, entry):
        self._load_books(entry.get_text())
