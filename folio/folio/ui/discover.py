"""
Discover page — personalised book recommendations from the local library.
"""

import threading
from datetime import date, datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import (
    get_series_continuations,
    get_more_from_favorite_authors,
    get_oldest_want_to_read,
    get_random_unread,
    get_or_create_default_profile,
)
from folio.paths import COVERS_DIR

THUMB_W, THUMB_H = 48, 72


def _load_thumb(book_id) -> GdkPixbuf.Pixbuf | None:
    if not book_id:
        return None
    path = COVERS_DIR / f"{book_id}.webp"
    if path.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), THUMB_W, THUMB_H)
        except Exception:
            pass
    return None


def _days_waiting(added_at: str) -> str:
    try:
        d = (date.today() - datetime.fromisoformat(added_at).date()).days
        if d == 0:
            return _("added today")
        if d == 1:
            return _("added yesterday")
        return ngettext("waiting {n} day", "waiting {n} days", d).format(n=d)
    except Exception:
        return ""


def _make_section_heading(title: str, subtitle: str = "") -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_margin_start(20)
    box.set_margin_top(20)
    box.set_margin_bottom(6)

    h = Gtk.Label(label=title)
    h.add_css_class("title-4")
    h.set_xalign(0)
    box.append(h)

    if subtitle:
        s = Gtk.Label(label=subtitle)
        s.add_css_class("dim-label")
        s.add_css_class("caption")
        s.set_xalign(0)
        box.append(s)

    return box


def _empty_row(msg: str) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    lbl = Gtk.Label(label=msg)
    lbl.add_css_class("dim-label")
    lbl.set_margin_top(16)
    lbl.set_margin_bottom(16)
    row.set_child(lbl)
    return row


class DiscoverPage(Gtk.Box):
    def __init__(self, on_open_book=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_open_book = on_open_book
        self._profile_id: int | None = None
        self._build_ui()

    def set_profile(self, profile_id: int):
        self._profile_id = profile_id

    def _get_profile(self) -> int:
        if self._profile_id is None:
            self._profile_id = get_or_create_default_profile()
        return self._profile_id

    def _build_ui(self):
        # Top bar with refresh
        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        topbar.set_margin_start(16); topbar.set_margin_end(16)
        topbar.set_margin_top(10); topbar.set_margin_bottom(10)

        self._spinner = Gtk.Spinner()
        topbar.append(self._spinner)

        self._status_lbl = Gtk.Label()
        self._status_lbl.add_css_class("dim-label")
        self._status_lbl.add_css_class("caption")
        self._status_lbl.set_hexpand(True)
        self._status_lbl.set_xalign(0)
        topbar.append(self._status_lbl)

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("circular")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text(_("Refresh recommendations"))
        refresh_btn.connect("clicked", lambda _b: self.refresh())
        topbar.append(refresh_btn)

        self.append(topbar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Scrollable content
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(self._content)
        self.append(scroll)

        # Section widgets (created once, populated on refresh)
        # Series continuations
        self.append_to_content(_make_section_heading(
            _("Continue the series"),
            _("Next books in series you have started"),
        ))
        self._series_list = self._make_list()

        # More from your authors
        self.append_to_content(_make_section_heading(
            _("More from your authors"),
            _("Unread books in your library by authors you enjoy"),
        ))
        self._authors_list = self._make_list()

        # Been waiting
        self.append_to_content(_make_section_heading(
            _("Been on your list a while"),
            _("Your oldest want-to-read picks"),
        ))
        self._waiting_list = self._make_list()

        # Random pick
        self.append_to_content(_make_section_heading(
            _("Something different"),
            _("Random unread books from your library"),
        ))
        self._random_list = self._make_list()

    def append_to_content(self, widget):
        self._content.append(widget)

    def _make_list(self) -> Gtk.ListBox:
        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        lb.set_margin_start(16); lb.set_margin_end(16)
        lb.set_margin_bottom(4)
        lb.connect("row-activated", self._on_row_activated)
        self._content.append(lb)
        return lb

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self, profile_id: int | None = None):
        if profile_id is not None:
            self._profile_id = profile_id
        self._spinner.start()
        self._status_lbl.set_label(_("Loading recommendations…"))
        pid = self._get_profile()
        threading.Thread(target=self._bg_load, args=(pid,), daemon=True).start()

    # ── Background loading ────────────────────────────────────────────────────

    def _bg_load(self, pid: int):
        series = get_series_continuations(pid)
        authors = get_more_from_favorite_authors(pid)
        waiting = get_oldest_want_to_read(pid)
        random_books = get_random_unread(pid)

        # Load covers for all book_ids
        all_ids = (
            [b["id"] for b in series] +
            [b["id"] for b in authors] +
            [b.get("book_id") for b in waiting if b.get("book_id")] +
            [b["id"] for b in random_books]
        )
        covers = {}
        for bid in set(all_ids):
            if bid:
                covers[bid] = _load_thumb(bid)

        GLib.idle_add(self._populate, series, authors, waiting, random_books, covers)

    # ── Populate ──────────────────────────────────────────────────────────────

    def _populate(self, series, authors, waiting, random_books, covers):
        self._spinner.stop()
        self._status_lbl.set_label("")

        self._fill_list(self._series_list, series,
                        covers, empty_msg=_("No series in progress."))
        self._fill_list(self._authors_list, authors,
                        covers, empty_msg=_("Read more books to get author recommendations."))
        self._fill_waiting(waiting, covers)
        self._fill_list(self._random_list, random_books,
                        covers, empty_msg=_("Your library is fully explored!"))

    def _fill_list(self, lb: Gtk.ListBox, books: list, covers: dict, empty_msg: str):
        while lb.get_first_child():
            lb.remove(lb.get_first_child())

        if not books:
            lb.append(_empty_row(empty_msg))
            return

        for book in books:
            bid = book.get("id")
            lb.append(self._make_book_row(book, covers.get(bid)))

    def _fill_waiting(self, entries: list, covers: dict):
        lb = self._waiting_list
        while lb.get_first_child():
            lb.remove(lb.get_first_child())

        if not entries:
            lb.append(_empty_row(_("Your want-to-read list is empty.")))
            return

        for e in entries:
            bid = e.get("book_id")
            lb.append(self._make_waiting_row(e, covers.get(bid)))

    def _make_book_row(self, book: dict, pb) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._book_id = book.get("id")
        row.set_activatable(bool(row._book_id))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(12); box.set_margin_end(12)

        thumb = Gtk.Picture()
        thumb.set_size_request(THUMB_W, THUMB_H)
        thumb.set_content_fit(Gtk.ContentFit.COVER)
        thumb.set_can_shrink(False)
        if pb:
            thumb.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        box.append(thumb)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        txt.set_hexpand(True)
        txt.set_valign(Gtk.Align.CENTER)

        title_lbl = Gtk.Label(label=book.get("title", ""))
        title_lbl.set_xalign(0); title_lbl.set_ellipsize(3)
        txt.append(title_lbl)

        if book.get("author"):
            a_lbl = Gtk.Label(label=book["author"])
            a_lbl.add_css_class("dim-label"); a_lbl.add_css_class("caption")
            a_lbl.set_xalign(0); a_lbl.set_ellipsize(3)
            txt.append(a_lbl)

        meta = []
        if book.get("series_name"):
            s = book["series_name"]
            if book.get("series_num"):
                s += "  ·  " + _("vol. {n}").format(n=book["series_num"])
            meta.append(s)
        if book.get("year"):
            meta.append(str(book["year"]))
        if book.get("pages"):
            meta.append(ngettext("{n} page", "{n} pages", book["pages"]).format(n=book["pages"]))
        if meta:
            m_lbl = Gtk.Label(label="  ·  ".join(meta))
            m_lbl.add_css_class("dim-label"); m_lbl.add_css_class("caption")
            m_lbl.set_xalign(0); m_lbl.set_ellipsize(3)
            txt.append(m_lbl)

        box.append(txt)
        row.set_child(box)
        return row

    def _make_waiting_row(self, entry: dict, pb) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._book_id = entry.get("book_id")
        row.set_activatable(bool(row._book_id))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(12); box.set_margin_end(12)

        thumb = Gtk.Picture()
        thumb.set_size_request(THUMB_W, THUMB_H)
        thumb.set_content_fit(Gtk.ContentFit.COVER)
        thumb.set_can_shrink(False)
        if pb:
            thumb.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        box.append(thumb)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        txt.set_hexpand(True)
        txt.set_valign(Gtk.Align.CENTER)

        title_lbl = Gtk.Label(label=entry.get("title", ""))
        title_lbl.set_xalign(0); title_lbl.set_ellipsize(3)
        txt.append(title_lbl)

        if entry.get("author"):
            a_lbl = Gtk.Label(label=entry["author"])
            a_lbl.add_css_class("dim-label"); a_lbl.add_css_class("caption")
            a_lbl.set_xalign(0); a_lbl.set_ellipsize(3)
            txt.append(a_lbl)

        waiting_str = _days_waiting(entry.get("added_at", ""))
        if waiting_str:
            w_lbl = Gtk.Label(label=waiting_str)
            w_lbl.add_css_class("dim-label"); w_lbl.add_css_class("caption")
            w_lbl.set_xalign(0)
            txt.append(w_lbl)

        box.append(txt)
        row.set_child(box)
        return row

    def _on_row_activated(self, _lb, row):
        bid = getattr(row, "_book_id", None)
        if bid and self._on_open_book:
            self._on_open_book(bid)
