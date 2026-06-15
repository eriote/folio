"""
Reading page — Reading / Want to Read / Read tabs, including external books.
"""

import threading
from datetime import date

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import (
    get_reading_list, set_reading_status, remove_from_reading_log,
    get_external_books, add_external_book, delete_external_book,
    get_or_create_default_profile,
)
from folio.paths import COVERS_DIR

THUMB_W, THUMB_H = 48, 72


def _load_thumb(book_id) -> GdkPixbuf.Pixbuf | None:
    if book_id is None:
        return None
    path = COVERS_DIR / f"{book_id}.webp"
    if path.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), THUMB_W, THUMB_H)
        except Exception:
            pass
    return None


class ReadingPage(Gtk.Box):
    def __init__(self, on_open_book=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_open_book = on_open_book
        self._profile_id: int | None = None
        self._build_ui()
        self.refresh()

    def set_profile(self, profile_id: int):
        self._profile_id = profile_id
        self.refresh()

    def _get_profile(self) -> int:
        if self._profile_id is None:
            self._profile_id = get_or_create_default_profile()
        return self._profile_id

    def _build_ui(self):
        # Header row with tab bar + "Add external" button
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_row.set_margin_top(12)
        header_row.set_margin_bottom(12)
        header_row.set_margin_start(16)
        header_row.set_margin_end(16)

        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tab_bar.add_css_class("linked")
        tab_bar.set_hexpand(True)
        tab_bar.set_halign(Gtk.Align.CENTER)
        header_row.append(tab_bar)

        add_ext_btn = Gtk.Button(label=_("+ Add external book"))
        add_ext_btn.add_css_class("pill")
        add_ext_btn.set_tooltip_text(_("Add a book read outside the library (paper, other app…)"))
        add_ext_btn.connect("clicked", self._on_add_external_clicked)
        header_row.append(add_ext_btn)

        self.append(header_row)

        self._tabs = {}
        for key, label in [
            ("reading",      _("Reading")),
            ("want_to_read", _("Want to read")),
            ("read",         _("Read")),
        ]:
            btn = Gtk.ToggleButton(label=label)
            btn.connect("toggled", self._on_tab_toggled, key)
            tab_bar.append(btn)
            self._tabs[key] = btn

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(150)
        self._stack.set_vexpand(True)
        self.append(self._stack)

        for key in ("reading", "want_to_read", "read"):
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            lb = Gtk.ListBox()
            lb.add_css_class("boxed-list")
            lb.set_selection_mode(Gtk.SelectionMode.NONE)
            lb.set_margin_top(8); lb.set_margin_bottom(16)
            lb.set_margin_start(24); lb.set_margin_end(24)
            scroll.set_child(lb)
            self._stack.add_named(scroll, key)
            setattr(self, f"_lb_{key}", lb)

        self._tabs["reading"].set_active(True)
        self._active_tab = "reading"

    def _on_tab_toggled(self, btn, key):
        if btn.get_active():
            for k, b in self._tabs.items():
                if k != key:
                    b.set_active(False)
            self._stack.set_visible_child_name(key)
            self._active_tab = key

    def refresh(self):
        for key in ("reading", "want_to_read", "read"):
            lb = getattr(self, f"_lb_{key}")
            while lb.get_first_child():
                lb.remove(lb.get_first_child())

        profile_id = self._get_profile()

        def _bg():
            data: dict[str, list] = {}
            for k in ("reading", "want_to_read", "read"):
                entries = get_reading_list(k, profile_id)
                ext = get_external_books(k, profile_id)
                combined = sorted(entries + ext,
                                  key=lambda e: e.get("added_at", ""), reverse=True)
                data[k] = combined
            covers = {}
            for entries in data.values():
                for e in entries:
                    bid = e.get("book_id")
                    if bid and bid not in covers:
                        covers[bid] = _load_thumb(bid)
            GLib.idle_add(self._populate, data, covers)

        threading.Thread(target=_bg, daemon=True).start()

    def _populate(self, data: dict, covers: dict):
        for status, entries in data.items():
            lb = getattr(self, f"_lb_{status}")
            if not entries:
                row = Gtk.ListBoxRow()
                row.set_activatable(False)
                lbl = Gtk.Label(label=_("Nothing here yet."))
                lbl.add_css_class("dim-label")
                lbl.set_margin_top(24); lbl.set_margin_bottom(24)
                row.set_child(lbl)
                lb.append(row)
                continue
            for entry in entries:
                row = self._make_row(entry, status, covers.get(entry.get("book_id")))
                lb.append(row)

    def _make_row(self, entry: dict, status: str, pb) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(bool(entry.get("book_id")))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(12); box.set_margin_end(8)

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

        title_lbl = Gtk.Label(label=entry["title"])
        title_lbl.set_xalign(0); title_lbl.set_ellipsize(3)
        txt.append(title_lbl)

        author_lbl = Gtk.Label(label=entry["author"])
        author_lbl.add_css_class("dim-label"); author_lbl.add_css_class("caption")
        author_lbl.set_xalign(0); author_lbl.set_ellipsize(3)
        txt.append(author_lbl)

        if status == "read" and entry.get("date_finished"):
            date_lbl = Gtk.Label(
                label=_("Finished: {date}").format(date=entry["date_finished"])
            )
            date_lbl.add_css_class("caption"); date_lbl.add_css_class("dim-label")
            date_lbl.set_xalign(0)
            txt.append(date_lbl)

        if entry.get("is_external"):
            ext_badge = Gtk.Label(label=_("external"))
            ext_badge.add_css_class("caption")
            ext_badge.add_css_class("dim-label")
            txt.append(ext_badge)

        box.append(txt)

        if status == "reading":
            done_btn = Gtk.Button(label=_("✓ Done"))
            done_btn.add_css_class("flat"); done_btn.add_css_class("suggested-action")
            done_btn.set_valign(Gtk.Align.CENTER)
            done_btn.connect("clicked", self._on_mark_done, entry)
            box.append(done_btn)

        if status == "want_to_read":
            start_btn = Gtk.Button(label=_("▶ Start"))
            start_btn.add_css_class("flat")
            start_btn.set_valign(Gtk.Align.CENTER)
            start_btn.connect("clicked", self._on_start_reading, entry)
            box.append(start_btn)

        rm_btn = Gtk.Button()
        rm_btn.set_icon_name("list-remove-symbolic")
        rm_btn.add_css_class("flat")
        rm_btn.set_valign(Gtk.Align.CENTER)
        rm_btn.set_tooltip_text(_("Remove from list"))
        rm_btn.connect("clicked", self._on_remove, entry, row)
        box.append(rm_btn)

        row.set_child(box)
        return row

    def _on_mark_done(self, _, entry):
        if entry.get("is_external"):
            from folio.database import update_external_book
            update_external_book(entry["id"], status="read",
                                 date_finished=date.today().isoformat())
        else:
            set_reading_status(entry["book_id"], "read",
                               entry["title"], entry["author"],
                               profile_id=self._get_profile())
        self.refresh()

    def _on_start_reading(self, _, entry):
        if entry.get("is_external"):
            from folio.database import update_external_book
            update_external_book(entry["id"], status="reading",
                                 date_started=date.today().isoformat())
        else:
            set_reading_status(entry["book_id"], "reading",
                               entry["title"], entry["author"],
                               profile_id=self._get_profile())
        self.refresh()

    def _on_remove(self, _, entry, row):
        if entry.get("is_external"):
            delete_external_book(entry["id"])
        else:
            remove_from_reading_log(entry["book_id"],
                                    profile_id=self._get_profile())
        self.refresh()

    def _on_add_external_clicked(self, _btn):
        dlg = _ExternalBookDialog(self.get_root())
        dlg.connect("response", self._on_external_dialog_response)
        dlg.show()

    def _on_external_dialog_response(self, dlg, response):
        if response == Gtk.ResponseType.OK:
            data = dlg.get_data()
            add_external_book(
                profile_id=self._get_profile(),
                **data,
            )
            self.refresh()
        dlg.destroy()


class _ExternalBookDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(
            title=_("Add external book"),
            transient_for=parent,
            modal=True,
        )
        self.set_default_size(400, -1)
        self.set_resizable(False)

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(20); box.set_margin_start(20)
        box.set_margin_end(20); box.set_margin_bottom(12)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)

        def _row(label_text, widget, row):
            lbl = Gtk.Label(label=label_text)
            lbl.set_xalign(1)
            lbl.add_css_class("dim-label")
            grid.attach(lbl, 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)

        self._title_e = Gtk.Entry()
        self._title_e.set_placeholder_text(_("Required"))
        _row(_("Title:"), self._title_e, 0)

        self._author_e = Gtk.Entry()
        _row(_("Author:"), self._author_e, 1)

        self._year_e = Gtk.SpinButton()
        self._year_e.set_range(1, 9999)
        self._year_e.set_increments(1, 10)
        self._year_e.set_value(date.today().year)
        _row(_("Year:"), self._year_e, 2)

        self._pages_e = Gtk.SpinButton()
        self._pages_e.set_range(0, 99999)
        self._pages_e.set_increments(1, 50)
        _row(_("Pages:"), self._pages_e, 3)

        self._status_dd = Gtk.DropDown.new_from_strings([
            _("Read"), _("Reading"), _("Want to read"),
        ])
        self._status_dd.set_selected(0)
        _row(_("Status:"), self._status_dd, 4)

        self._date_e = Gtk.Entry()
        self._date_e.set_placeholder_text("YYYY-MM-DD")
        self._date_e.set_text(date.today().isoformat())
        _row(_("Date read:"), self._date_e, 5)

        self._rating_spin = Gtk.SpinButton()
        self._rating_spin.set_range(0, 5)
        self._rating_spin.set_increments(1, 1)
        _row(_("Rating (0–5):"), self._rating_spin, 6)

        self._notes_e = Gtk.Entry()
        _row(_("Notes:"), self._notes_e, 7)

        box.append(grid)

        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        ok = self.add_button(_("Add"), Gtk.ResponseType.OK)
        ok.add_css_class("suggested-action")

    def get_data(self) -> dict:
        status_map = {0: "read", 1: "reading", 2: "want_to_read"}
        status = status_map.get(self._status_dd.get_selected(), "read")
        date_val = self._date_e.get_text().strip() or None
        rating = int(self._rating_spin.get_value()) or None
        pages = int(self._pages_e.get_value()) or None
        year = int(self._year_e.get_value()) or None
        return {
            "title": self._title_e.get_text().strip(),
            "author": self._author_e.get_text().strip(),
            "year": year,
            "pages": pages,
            "status": status,
            "date_finished": date_val if status == "read" else None,
            "date_started": date_val if status == "reading" else None,
            "rating": rating,
            "notes": self._notes_e.get_text().strip(),
        }
