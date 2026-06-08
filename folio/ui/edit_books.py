"""
Edit-books page — searchable book list + metadata form with cover editor.
"""

import io
import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk
from PIL import Image

from folio.database import (
    get_all_books, get_book, update_book, set_book_authors, delete_book,
)
from folio.paths import COVERS_DIR

EDIT_COVER_W, EDIT_COVER_H = 150, 225


def _pixbuf_from_bytes(data: bytes, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
        return pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


def _fetch_cover_online(title: str, author: str) -> bytes | None:
    """Query Open Library for a cover. Returns image bytes or None."""
    try:
        params = urllib.parse.urlencode({"title": title, "author": author, "limit": "3"})
        req = urllib.request.Request(
            f"https://openlibrary.org/search.json?{params}",
            headers={"User-Agent": "Folio/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            docs = json.loads(r.read()).get("docs", [])
        for doc in docs:
            cover_id = doc.get("cover_i")
            if not cover_id:
                continue
            with urllib.request.urlopen(
                f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg", timeout=8
            ) as r:
                data = r.read()
            if len(data) > 5000:
                return data
    except Exception:
        pass
    return None


def _save_cover_file(book_id: int, image_bytes: bytes) -> None:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((300, 450), Image.LANCZOS)
    img.save(COVERS_DIR / f"{book_id}.webp", "WEBP", quality=85)


class EditPage(Gtk.Box):
    def __init__(self, on_book_deleted=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_book_deleted = on_book_deleted
        self._current_id = None
        self._filter_text = ""
        self._edit_cover_bytes = None  # pending cover to save
        self._build_ui()

    def _build_ui(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(240)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_vexpand(True)
        self.append(paned)

        # ── Left: search + book list ───────────────────────────────────────
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Filter books…"))
        self._search.set_margin_top(12)
        self._search.set_margin_start(8)
        self._search.set_margin_end(8)
        self._search.set_margin_bottom(8)
        self._search.connect("search-changed", self._on_filter_changed)
        left.append(self._search)
        left.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        self._book_list = Gtk.ListBox()
        self._book_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._book_list.set_filter_func(self._filter_func)
        self._book_list.connect("row-selected", self._on_row_selected)
        list_scroll.set_child(self._book_list)
        left.append(list_scroll)
        paned.set_start_child(left)

        # ── Right: form ───────────────────────────────────────────────────
        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right_scroll.set_hexpand(True)
        right_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_scroll.set_child(right_outer)
        paned.set_end_child(right_scroll)

        # Empty state
        self._empty_box = Gtk.Box()
        self._empty_box.set_vexpand(True)
        empty_lbl = Gtk.Label(label=_("Select a book from the list"))
        empty_lbl.add_css_class("dim-label")
        empty_lbl.set_valign(Gtk.Align.CENTER)
        empty_lbl.set_vexpand(True)
        self._empty_box.append(empty_lbl)
        right_outer.append(self._empty_box)

        # Form body (hidden until selection)
        self._form_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self._form_box.set_visible(False)
        self._form_box.set_margin_top(24)
        self._form_box.set_margin_start(24)
        self._form_box.set_margin_end(24)
        self._form_box.set_margin_bottom(24)
        right_outer.append(self._form_box)

        # ── Cover column ──────────────────────────────────────────────────
        cover_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cover_col.set_valign(Gtk.Align.START)
        self._form_box.append(cover_col)

        frame = Gtk.Frame()
        frame.set_size_request(EDIT_COVER_W, EDIT_COVER_H)
        self._cover_pic = Gtk.Picture()
        self._cover_pic.set_size_request(EDIT_COVER_W, EDIT_COVER_H)
        self._cover_pic.set_content_fit(Gtk.ContentFit.COVER)
        self._cover_pic.set_can_shrink(False)
        frame.set_child(self._cover_pic)
        cover_col.append(frame)

        self._cover_status = Gtk.Label(label="")
        self._cover_status.add_css_class("dim-label")
        self._cover_status.add_css_class("caption")
        self._cover_status.set_wrap(True)
        self._cover_status.set_max_width_chars(18)
        self._cover_status.set_xalign(0.5)
        cover_col.append(self._cover_status)

        pick_btn = Gtk.Button(label=_("Choose image…"))
        pick_btn.connect("clicked", self._on_pick_cover)
        cover_col.append(pick_btn)

        fetch_btn = Gtk.Button(label=_("Search online"))
        fetch_btn.connect("clicked", self._on_fetch_cover)
        cover_col.append(fetch_btn)

        # ── Fields column ─────────────────────────────────────────────────
        fields_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        fields_col.set_hexpand(True)
        fields_col.set_valign(Gtk.Align.START)
        self._form_box.append(fields_col)

        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(12)
        fields_col.append(grid)

        def _lbl(text):
            l = Gtk.Label(label=text)
            l.set_xalign(1)
            l.add_css_class("dim-label")
            return l

        grid.attach(_lbl(_("Title")), 0, 0, 1, 1)
        self._e_title = Gtk.Entry()
        self._e_title.set_hexpand(True)
        grid.attach(self._e_title, 1, 0, 3, 1)

        grid.attach(_lbl(_("Authors")), 0, 1, 1, 1)
        self._e_authors = Gtk.Entry()
        self._e_authors.set_placeholder_text(_("separated by comma"))
        self._e_authors.set_hexpand(True)
        grid.attach(self._e_authors, 1, 1, 3, 1)

        grid.attach(_lbl(_("Year")), 0, 2, 1, 1)
        self._e_year = Gtk.Entry()
        self._e_year.set_max_length(4)
        self._e_year.set_width_chars(6)
        grid.attach(self._e_year, 1, 2, 1, 1)

        grid.attach(_lbl(_("Pages")), 2, 2, 1, 1)
        self._e_pages = Gtk.Entry()
        self._e_pages.set_max_length(6)
        self._e_pages.set_width_chars(7)
        grid.attach(self._e_pages, 3, 2, 1, 1)

        grid.attach(_lbl(_("Series")), 0, 3, 1, 1)
        self._e_series = Gtk.Entry()
        self._e_series.set_hexpand(True)
        grid.attach(self._e_series, 1, 3, 2, 1)

        grid.attach(_lbl(_("Vol.")), 3, 3, 1, 1)
        self._e_series_num = Gtk.Entry()
        self._e_series_num.set_width_chars(5)
        grid.attach(self._e_series_num, 3, 3, 1, 1)

        grid.attach(_lbl(_("Description")), 0, 4, 1, 1)
        desc_scroll = Gtk.ScrolledWindow()
        desc_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        desc_scroll.set_min_content_height(120)
        desc_scroll.set_max_content_height(200)
        desc_scroll.set_propagate_natural_height(True)
        self._tv_desc = Gtk.TextView()
        self._tv_desc.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv_desc.set_top_margin(6)
        self._tv_desc.set_left_margin(6)
        self._tv_desc.set_right_margin(6)
        self._tv_desc.set_bottom_margin(6)
        desc_scroll.set_child(self._tv_desc)
        desc_scroll.set_hexpand(True)
        grid.attach(desc_scroll, 1, 4, 3, 1)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(16)

        self._save_btn = Gtk.Button(label=_("Save changes"))
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.add_css_class("pill")
        self._save_btn.connect("clicked", self._on_save)
        btn_row.append(self._save_btn)

        self._del_btn = Gtk.Button(label=_("Delete book"))
        self._del_btn.add_css_class("destructive-action")
        self._del_btn.add_css_class("pill")
        self._del_btn.connect("clicked", self._on_delete)
        btn_row.append(self._del_btn)

        self._status_lbl = Gtk.Label()
        self._status_lbl.add_css_class("dim-label")
        self._status_lbl.add_css_class("caption")
        self._status_lbl.set_margin_start(8)
        btn_row.append(self._status_lbl)

        fields_col.append(btn_row)

    # ── Public API ────────────────────────────────────────────────────────

    def refresh(self):
        def _bg():
            books = get_all_books()
            GLib.idle_add(self._populate_list, books)
        threading.Thread(target=_bg, daemon=True).start()

    # ── Book list ─────────────────────────────────────────────────────────

    def _populate_list(self, books: list):
        while self._book_list.get_first_child():
            self._book_list.remove(self._book_list.get_first_child())
        for book in books:
            row = self._make_list_row(book)
            self._book_list.append(row)

    def _make_list_row(self, book: dict) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._book_id = book["id"]
        row._title_text = book["title"].lower()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(10)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        txt.set_hexpand(True)
        txt.set_valign(Gtk.Align.CENTER)

        title_lbl = Gtk.Label(label=book["title"])
        title_lbl.set_xalign(0)
        title_lbl.set_ellipsize(3)
        txt.append(title_lbl)

        if book.get("author"):
            author_lbl = Gtk.Label(label=book["author"])
            author_lbl.add_css_class("dim-label")
            author_lbl.add_css_class("caption")
            author_lbl.set_xalign(0)
            author_lbl.set_ellipsize(3)
            txt.append(author_lbl)

        box.append(txt)
        row.set_child(box)
        return row

    def _on_filter_changed(self, entry):
        self._filter_text = entry.get_text().lower()
        self._book_list.invalidate_filter()

    def _filter_func(self, row) -> bool:
        if not self._filter_text:
            return True
        return self._filter_text in getattr(row, "_title_text", "")

    # ── Form ──────────────────────────────────────────────────────────────

    def _on_row_selected(self, lb, row):
        if row is None:
            return
        book_id = getattr(row, "_book_id", None)
        if book_id is None:
            return
        self._current_id = book_id
        self._edit_cover_bytes = None
        self._status_lbl.set_label("")

        def _bg():
            book = get_book(book_id)
            cover_path = COVERS_DIR / f"{book_id}.webp"
            pb = None
            if cover_path.exists():
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        str(cover_path), EDIT_COVER_W, EDIT_COVER_H
                    )
                except Exception:
                    pass
            GLib.idle_add(self._fill_form, book, pb)
        threading.Thread(target=_bg, daemon=True).start()

    def _fill_form(self, book: dict, pb):
        self._empty_box.set_visible(False)
        self._form_box.set_visible(True)

        if pb:
            self._cover_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
            self._cover_status.set_label(_("Current cover"))
        else:
            self._cover_pic.set_paintable(None)
            self._cover_status.set_label(_("No cover"))

        self._e_title.set_text(book.get("title") or "")
        authors = ", ".join(a["name"] for a in book.get("authors", []))
        self._e_authors.set_text(authors)
        self._e_year.set_text(str(book["year"]) if book.get("year") else "")
        self._e_pages.set_text(str(book["pages"]) if book.get("pages") else "")
        self._e_series.set_text(book.get("series_name") or "")
        self._e_series_num.set_text(book.get("series_num") or "")
        self._tv_desc.get_buffer().set_text(book.get("description") or "")

    # ── Cover editing ─────────────────────────────────────────────────────

    def _on_pick_cover(self, _):
        if not self._current_id:
            return
        dialog = Gtk.FileChooserDialog(
            title=_("Select cover image"),
            transient_for=self.get_root(),
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Use this image"), Gtk.ResponseType.ACCEPT)
        f = Gtk.FileFilter()
        f.set_name(_("Images (jpg, png, webp)"))
        for p in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            f.add_pattern(p)
        dialog.add_filter(f)
        dialog.connect("response", self._on_pick_cover_response)
        dialog.show()

    def _on_pick_cover_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            path = Path(dialog.get_file().get_path())
            dialog.destroy()
            try:
                data = path.read_bytes()
                pb = _pixbuf_from_bytes(data, EDIT_COVER_W, EDIT_COVER_H)
                if pb:
                    self._edit_cover_bytes = data
                    self._cover_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
                    self._cover_status.set_label(_("New cover (unsaved)"))
            except Exception:
                self._cover_status.set_label(_("Error reading image"))
        else:
            dialog.destroy()

    def _on_fetch_cover(self, _):
        if not self._current_id:
            return
        title = self._e_title.get_text().strip()
        author = self._e_authors.get_text().strip()
        self._cover_status.set_label(_("Searching online…"))

        def _bg():
            data = _fetch_cover_online(title, author)
            def _done():
                if data:
                    pb = _pixbuf_from_bytes(data, EDIT_COVER_W, EDIT_COVER_H)
                    if pb:
                        self._edit_cover_bytes = data
                        self._cover_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
                        self._cover_status.set_label(_("Cover downloaded (unsaved)"))
                        return
                self._cover_status.set_label(_("Not found online"))
            GLib.idle_add(_done)
        threading.Thread(target=_bg, daemon=True).start()

    # ── Save / Delete ─────────────────────────────────────────────────────

    def _on_save(self, _):
        if not self._current_id:
            return

        title = self._e_title.get_text().strip()
        if not title:
            self._status_lbl.set_label(_("Title cannot be empty."))
            return

        authors = [a.strip() for a in self._e_authors.get_text().split(",") if a.strip()] or [_("Unknown")]
        year_txt = self._e_year.get_text().strip()
        pages_txt = self._e_pages.get_text().strip()
        year = int(year_txt) if year_txt.isdigit() else None
        pages = int(pages_txt) if pages_txt.isdigit() else None
        buf = self._tv_desc.get_buffer()
        desc = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

        cover_bytes = self._edit_cover_bytes
        book_id = self._current_id

        def _bg():
            update_book(
                book_id,
                title=title, year=year, pages=pages, description=desc,
                series=self._e_series.get_text().strip(),
                series_num=self._e_series_num.get_text().strip(),
            )
            set_book_authors(book_id, authors)
            if cover_bytes:
                try:
                    _save_cover_file(book_id, cover_bytes)
                except Exception:
                    pass
            GLib.idle_add(self._on_save_done, title, authors)
        threading.Thread(target=_bg, daemon=True).start()

    def _on_save_done(self, title, authors):
        self._edit_cover_bytes = None
        if _("unsaved") in self._cover_status.get_label():
            self._cover_status.set_label(_("Current cover"))
        self._status_lbl.set_label(_("Saved."))

        row = self._book_list.get_selected_row()
        if row:
            row._title_text = title.lower()
            box = row.get_child()
            if box:
                txt_box = box.get_first_child()
                if txt_box:
                    lbl = txt_box.get_first_child()
                    if lbl:
                        lbl.set_label(title)
                    lbl2 = lbl.get_next_sibling() if lbl else None
                    if lbl2:
                        lbl2.set_label(", ".join(authors))

    def _on_delete(self, _):
        if not self._current_id:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self.get_root(),
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Delete this book?"),
            secondary_text=_("It will be removed from the library. The epub file will not be deleted."),
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        btn = dialog.add_button(_("Delete"), Gtk.ResponseType.ACCEPT)
        btn.add_css_class("destructive-action")
        dialog.connect("response", self._on_delete_confirmed)
        dialog.show()

    def _on_delete_confirmed(self, dialog, response):
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT:
            return
        book_id = self._current_id
        delete_book(book_id)
        self._current_id = None
        self._edit_cover_bytes = None
        self._form_box.set_visible(False)
        self._empty_box.set_visible(True)

        row = self._book_list.get_selected_row()
        if row:
            self._book_list.remove(row)

        if self._on_book_deleted:
            self._on_book_deleted(book_id)
