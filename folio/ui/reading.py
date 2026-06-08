"""
Reading page — Reading / Want to Read / Read tabs.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import (
    get_reading_list, set_reading_status, remove_from_reading_log
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
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tab_bar.add_css_class("linked")
        tab_bar.set_halign(Gtk.Align.CENTER)
        tab_bar.set_margin_top(16)
        tab_bar.set_margin_bottom(16)
        self.append(tab_bar)

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

        def _bg():
            data = {k: get_reading_list(k) for k in ("reading", "want_to_read", "read")}
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
        set_reading_status(entry["book_id"], "read",
                           entry["title"], entry["author"])
        self.refresh()

    def _on_start_reading(self, _, entry):
        set_reading_status(entry["book_id"], "reading",
                           entry["title"], entry["author"])
        self.refresh()

    def _on_remove(self, _, entry, row):
        remove_from_reading_log(entry["book_id"])
        self.refresh()
