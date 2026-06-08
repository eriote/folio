"""
Main application window.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import (
    get_all_books, search_books, count_books,
    get_books_by_author, get_books_by_series,
)
from folio.paths import COVERS_DIR
from folio.ui.book_detail import BookDetail
from folio.ui.reading import ReadingPage

CARD_W = 150
CARD_H = 225

_SIDEBAR_CSS = """
#folio-sidebar {
    background-color: mix(@window_bg_color, @window_fg_color, 0.05);
    border-right: 1px solid mix(@window_bg_color, @window_fg_color, 0.12);
}
"""

_SIDEBAR_TABS = [
    ("library",  "Biblioteca",  "view-grid-symbolic",         True),
    ("reading",  "Lecturas",    "bookmark-symbolic",          True),
    ("discover", "Descubrir",   "starred-symbolic",           False),
    ("settings", "Ajustes",     "preferences-system-symbolic", False),
]


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
        self._active_root = "library"
        self._detail_came_from = "grid"
        self._apply_css()
        self._build_ui()
        self._load_books()

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(_SIDEBAR_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        # ── HeaderBar ─────────────────────────────────────────────────────
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        self._back_btn = Gtk.Button()
        self._back_btn.set_icon_name("go-previous-symbolic")
        self._back_btn.set_tooltip_text("Volver")
        self._back_btn.connect("clicked", self._on_back)
        self._back_btn.set_visible(False)
        header.pack_start(self._back_btn)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Buscar…")
        self._search.set_size_request(220, -1)
        self._search.connect("search-changed", self._on_search_changed)
        header.pack_end(self._search)

        self._count_lbl = Gtk.Label()
        self._count_lbl.add_css_class("dim-label")
        self._count_lbl.add_css_class("caption")
        header.pack_end(self._count_lbl)

        # ── Paned: sidebar + stack ─────────────────────────────────────────
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(170)
        self.set_child(paned)

        # Sidebar
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.set_name("folio-sidebar")

        self._sidebar_list = Gtk.ListBox()
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.add_css_class("navigation-sidebar")
        self._sidebar_list.set_vexpand(True)
        self._sidebar_list.connect("row-activated", self._on_sidebar_activated)

        self._sidebar_rows = {}
        for key, label, icon, enabled in _SIDEBAR_TABS:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row_box.set_margin_start(10)
            row_box.set_margin_end(10)
            row_box.set_margin_top(9)
            row_box.set_margin_bottom(9)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(16)
            row_box.append(img)
            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            lbl.set_hexpand(True)
            row_box.append(lbl)
            row = Gtk.ListBoxRow()
            row.set_child(row_box)
            row.set_sensitive(enabled)
            self._sidebar_list.append(row)
            self._sidebar_rows[key] = row

        sidebar_box.append(self._sidebar_list)
        paned.set_start_child(sidebar_box)

        # ── Stack ─────────────────────────────────────────────────────────
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        self._stack.set_transition_duration(180)
        paned.set_end_child(self._stack)

        # Sync sidebar selection when stack changes programmatically
        self._stack.connect("notify::visible-child-name", self._on_stack_page_changed)

        # Library grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._flow = Gtk.FlowBox()
        self._flow.set_valign(Gtk.Align.START)
        self._flow.set_max_children_per_line(12)
        self._flow.set_min_children_per_line(2)
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_margin_top(16)
        self._flow.set_margin_start(16)
        self._flow.set_margin_end(16)
        self._flow.connect("child-activated", self._on_card_activated)
        scroll.set_child(self._flow)
        self._stack.add_named(scroll, "grid")

        # Book detail
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._detail = BookDetail(
            on_open_author=self._open_author_page,
            on_open_series=self._open_series_page,
        )
        detail_scroll.set_child(self._detail)
        self._stack.add_named(detail_scroll, "detail")

        # Reading
        self._reading_page = ReadingPage(on_open_book=self._open_book_detail)
        self._stack.add_named(self._reading_page, "reading")

        # Collection (author / series)
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

        # Select initial sidebar row
        self._sidebar_list.select_row(self._sidebar_rows["library"])

    # ── Navigation ────────────────────────────────────────────────────────

    def _on_sidebar_activated(self, lb, row):
        for key, r in self._sidebar_rows.items():
            if r == row:
                self._active_root = key
                root_page = "grid" if key == "library" else key
                self._stack.set_visible_child_name(root_page)
                self._back_btn.set_visible(False)
                self._search.set_visible(key == "library")
                self._count_lbl.set_visible(key == "library")
                if key == "reading":
                    self._reading_page.refresh()
                break

    def _on_stack_page_changed(self, stack, _param):
        name = stack.get_visible_child_name()
        root = "library" if name in ("grid", "detail", "collection") else name
        if root in self._sidebar_rows:
            self._sidebar_list.select_row(self._sidebar_rows[root])

    def _open_book_detail(self, book_id: int):
        self._detail_came_from = self._stack.get_visible_child_name()
        self._detail.load_book(book_id)
        self._stack.set_visible_child_name("detail")
        self._back_btn.set_visible(True)
        self._search.set_visible(False)
        self._count_lbl.set_visible(False)

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
        self._detail_came_from = self._stack.get_visible_child_name()
        self._stack.set_visible_child_name("collection")
        self._back_btn.set_visible(True)
        self._search.set_visible(False)
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

    def _on_back(self, _):
        prev = self._detail_came_from
        self._stack.set_visible_child_name(prev)
        at_root = prev in ("grid", "reading")
        self._back_btn.set_visible(not at_root)
        self._search.set_visible(prev == "grid")
        self._count_lbl.set_visible(prev == "grid")
        if prev == "reading":
            self._reading_page.refresh()

    # ── Library grid ──────────────────────────────────────────────────────

    def _load_books(self, query: str = ""):
        while self._flow.get_first_child():
            self._flow.remove(self._flow.get_first_child())
        def _bg():
            books = search_books(query) if query.strip() else get_all_books()
            GLib.idle_add(self._populate, books, query)
        threading.Thread(target=_bg, daemon=True).start()

    def _populate(self, books: list, query: str):
        total = count_books()
        self._count_lbl.set_label(
            f"{len(books)} de {total} libros" if query else f"{total} libros"
        )
        for book in books:
            card = BookCard(book)
            self._flow.append(card)
            card.load_cover_async()

    def _on_card_activated(self, flowbox, child):
        card = child.get_child()
        if isinstance(card, BookCard):
            self._open_book_detail(card.book["id"])

    def _on_coll_card_activated(self, flowbox, child):
        card = child.get_child()
        if isinstance(card, BookCard):
            self._open_book_detail(card.book["id"])

    def _on_search_changed(self, entry):
        self._load_books(entry.get_text())
