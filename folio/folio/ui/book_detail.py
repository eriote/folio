"""
Book detail page — shown when the user clicks a card in the grid.
"""

import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

from folio.database import (
    get_book, get_reading_status, set_reading_status, remove_from_reading_log
)
from folio.devices import connected_devices, send_book_to_device
from folio.paths import COVERS_DIR

COVER_W, COVER_H = 300, 450

STATUS_LABELS = {
    "reading":      "Reading",
    "read":         "Read ✓",
    "want_to_read": "Want to read",
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
    def __init__(self, on_open_author=None, on_open_series=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=32)
        self.set_margin_top(32); self.set_margin_bottom(32)
        self.set_margin_start(40); self.set_margin_end(40)
        self._book = None
        self._on_open_author = on_open_author
        self._on_open_series = on_open_series
        self._build_ui()

    def _build_ui(self):
        self._cover = Gtk.Picture()
        self._cover.set_size_request(COVER_W, COVER_H)
        self._cover.set_content_fit(Gtk.ContentFit.COVER)
        self._cover.set_can_shrink(False)
        self._cover.set_valign(Gtk.Align.START)
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder()))
        self.append(self._cover)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_hexpand(True)
        right.set_valign(Gtk.Align.START)
        self.append(right)

        self._title_lbl = Gtk.Label()
        self._title_lbl.add_css_class("title-2")
        self._title_lbl.set_wrap(True)
        self._title_lbl.set_xalign(0)
        right.append(self._title_lbl)

        self._author_btn = Gtk.Button()
        self._author_btn.add_css_class("flat")
        self._author_btn.set_halign(Gtk.Align.START)
        self._author_btn.set_margin_top(2)
        self._author_lbl = Gtk.Label()
        self._author_lbl.add_css_class("heading")
        self._author_btn.set_child(self._author_lbl)
        self._author_btn.connect("clicked", self._on_author_clicked)
        right.append(self._author_btn)

        self._series_btn = Gtk.Button()
        self._series_btn.add_css_class("flat")
        self._series_btn.set_halign(Gtk.Align.START)
        self._series_btn.set_visible(False)
        self._series_lbl = Gtk.Label()
        self._series_lbl.add_css_class("dim-label")
        self._series_btn.set_child(self._series_lbl)
        self._series_btn.connect("clicked", self._on_series_clicked)
        right.append(self._series_btn)

        self._meta_lbl = Gtk.Label()
        self._meta_lbl.add_css_class("dim-label")
        self._meta_lbl.add_css_class("caption")
        self._meta_lbl.set_xalign(0)
        self._meta_lbl.set_margin_top(4)
        self._meta_lbl.set_margin_bottom(8)
        right.append(self._meta_lbl)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_bottom(8)

        self._read_btn = Gtk.Button(label=_("▶ Start reading"))
        self._read_btn.add_css_class("suggested-action")
        self._read_btn.add_css_class("pill")
        self._read_btn.connect("clicked", self._on_read_clicked)
        btn_row.append(self._read_btn)

        self._want_btn = Gtk.Button(label=_("+ Want to read"))
        self._want_btn.add_css_class("pill")
        self._want_btn.connect("clicked", self._on_want_clicked)
        btn_row.append(self._want_btn)

        self._remove_btn = Gtk.Button(label=_("✕ Remove"))
        self._remove_btn.add_css_class("pill")
        self._remove_btn.add_css_class("destructive-action")
        self._remove_btn.set_visible(False)
        self._remove_btn.connect("clicked", self._on_remove_clicked)
        btn_row.append(self._remove_btn)

        self._send_btn = Gtk.Button(label=_("Send to device"))
        self._send_btn.set_icon_name("phone-symbolic")
        self._send_btn.add_css_class("pill")
        self._send_btn.connect("clicked", self._on_send_clicked)
        btn_row.append(self._send_btn)

        right.append(btn_row)

        self._status_lbl = Gtk.Label()
        self._status_lbl.add_css_class("dim-label")
        self._status_lbl.add_css_class("caption")
        self._status_lbl.set_xalign(0)
        self._status_lbl.set_visible(False)
        self._status_lbl.set_margin_bottom(8)
        right.append(self._status_lbl)

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

    def load_book(self, book_id: int):
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder()))
        self._book = None

        def _bg():
            book   = get_book(book_id)
            pb     = _load_cover(book_id)
            status = get_reading_status(book_id)
            GLib.idle_add(self._populate, book, pb, status)

        threading.Thread(target=_bg, daemon=True).start()

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
            label = _("Series: {series}").format(series=series)
            if series_num:
                label += "  ·  " + _("vol. {num}").format(num=series_num)
            self._series_lbl.set_label(label)
            self._series_btn.set_visible(True)
        else:
            self._series_btn.set_visible(False)

        parts = []
        if book.get("year"):
            parts.append(str(book["year"]))
        if book.get("pages"):
            parts.append(_("{pages} pages").format(pages=book["pages"]))
        self._meta_lbl.set_label("  ·  ".join(parts))

        self._desc_lbl.set_label(book.get("description") or _("No description."))

        self._update_status_ui(status)

    def _update_status_ui(self, status: str | None):
        if status:
            label = _(STATUS_LABELS.get(status, status))
            self._status_lbl.set_label(_("Status: {status}").format(status=label))
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
        set_reading_status(self._book["id"], "reading",
                           self._book["title"], self._author_str())
        self._update_status_ui("reading")

    def _on_want_clicked(self, _):
        if not self._book:
            return
        set_reading_status(self._book["id"], "want_to_read",
                           self._book["title"], self._author_str())
        self._update_status_ui("want_to_read")

    def _on_remove_clicked(self, _):
        if not self._book:
            return
        remove_from_reading_log(self._book["id"])
        self._update_status_ui(None)

    def _on_author_clicked(self, _):
        if not self._book or not self._on_open_author:
            return
        authors = [a["name"] for a in self._book.get("authors", [])]
        if authors:
            self._on_open_author(authors[0])

    def _on_series_clicked(self, _):
        if not self._book or not self._on_open_series:
            return
        series = self._book.get("series_name") or ""
        if series:
            self._on_open_series(series)

    def _on_send_clicked(self, _btn):
        if not self._book:
            return
        devices = connected_devices()
        if not devices:
            dlg = Gtk.MessageDialog(
                transient_for=self.get_root(), modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=_("No devices connected."),
            )
            dlg.connect("response", lambda d, _r: d.destroy())
            dlg.show()
            return

        dlg = Gtk.Dialog(
            title=_("Send to device"),
            transient_for=self.get_root(),
            modal=True,
        )
        dlg.set_default_size(300, 200)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12); box.set_margin_start(12)
        box.set_margin_end(12); box.set_margin_bottom(8)

        lbl = Gtk.Label(label=_("Choose a device:"))
        lbl.set_xalign(0)
        box.append(lbl)

        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.SINGLE)
        lb.add_css_class("boxed-list")
        for dev in devices:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(spacing=10)
            row_box.set_margin_top(8); row_box.set_margin_bottom(8)
            row_box.set_margin_start(12); row_box.set_margin_end(12)
            img = Gtk.Image.new_from_icon_name("phone-symbolic")
            img.set_pixel_size(16)
            row_box.append(img)
            row_box.append(Gtk.Label(label=dev["name"]))
            row.set_child(row_box)
            row._device = dev
            lb.append(row)
        lb.select_row(lb.get_row_at_index(0))
        box.append(lb)

        progress_box = Gtk.Box(spacing=8)
        progress_box.set_visible(False)
        spinner = Gtk.Spinner()
        spinner.start()
        progress_box.append(spinner)
        progress_lbl = Gtk.Label(label=_("Sending…"))
        progress_lbl.add_css_class("dim-label")
        progress_box.append(progress_lbl)
        box.append(progress_box)

        cancel_btn = dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        send_btn = dlg.add_button(_("Send"), Gtk.ResponseType.ACCEPT)
        send_btn.add_css_class("suggested-action")

        book = self._book

        def _on_resp(d, resp):
            if resp != Gtk.ResponseType.ACCEPT:
                d.destroy()
                return

            sel = lb.get_selected_row()
            if not sel:
                d.destroy()
                return

            device = sel._device
            lb.set_sensitive(False)
            cancel_btn.set_sensitive(False)
            send_btn.set_sensitive(False)
            progress_box.set_visible(True)

            def _bg():
                size = 0
                try:
                    from pathlib import Path as _Path
                    size = _Path(book["epub_path"]).stat().st_size
                except Exception:
                    pass

                def _progress(sent, total):
                    if total > 0:
                        GLib.idle_add(
                            progress_lbl.set_label,
                            _("Sending… {pct}%").format(pct=int(sent * 100 / total)),
                        )

                ok_result, msg = send_book_to_device(
                    book["epub_path"], device,
                    on_progress=_progress if size > 0 else None,
                )
                GLib.idle_add(_done, ok_result, msg, d)

            def _done(ok_result, msg, dialog):
                dialog.destroy()
                info = Gtk.MessageDialog(
                    transient_for=self.get_root(), modal=True,
                    message_type=Gtk.MessageType.INFO if ok_result else Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text=msg,
                )
                info.connect("response", lambda d2, _r: d2.destroy())
                info.show()

            threading.Thread(target=_bg, daemon=True).start()

        dlg.connect("response", _on_resp)
        dlg.show()
