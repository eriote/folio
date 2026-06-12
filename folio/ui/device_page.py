"""
Device page: browse files on a connected e-reader, import them and read
KoReader reading statistics.
"""

import sqlite3
import tempfile
import threading
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from folio.database import get_all_books
from folio.devices import (
    list_device_files,
    delete_device_file,
    download_device_file,
    get_koreader_db_bytes,
)
from folio.scanner import import_epub


def _norm(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _fmt_minutes(m: int) -> str:
    if m < 60:
        return ngettext("{n} min", "{n} min", m).format(n=m)
    h = m // 60
    mins = m % 60
    if mins:
        return _("{h}h {m}m").format(h=h, m=mins)
    return ngettext("{h} hour", "{h} hours", h).format(h=h)


def _days_ago(ts: int) -> str:
    try:
        d = (date.today() - datetime.fromtimestamp(ts).date()).days
    except Exception:
        return ""
    if d == 0:
        return _("today")
    if d == 1:
        return _("yesterday")
    return ngettext("{n} day ago", "{n} days ago", d).format(n=d)


# ── File row ──────────────────────────────────────────────────────────────────

class _FileRow(Gtk.ListBoxRow):
    def __init__(self, fi: dict):
        super().__init__()
        self._fi = fi
        self._check = Gtk.CheckButton()

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.set_margin_start(8)
        outer.set_margin_end(12)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        outer.append(self._check)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.set_hexpand(True)

        name_lbl = Gtk.Label(label=fi.get("name", ""))
        name_lbl.set_xalign(0)
        name_lbl.set_ellipsize(3)
        info.append(name_lbl)

        meta_parts = []
        if fi.get("size"):
            meta_parts.append(_fmt_size(fi["size"]))
        if fi.get("mtime"):
            try:
                dt = datetime.fromtimestamp(fi["mtime"])
                meta_parts.append(dt.strftime("%Y-%m-%d"))
            except Exception:
                pass
        meta_parts.append(fi.get("proto", ""))

        meta_lbl = Gtk.Label(label="  ·  ".join(p for p in meta_parts if p))
        meta_lbl.set_xalign(0)
        meta_lbl.add_css_class("dim-label")
        meta_lbl.add_css_class("caption")
        info.append(meta_lbl)

        outer.append(info)

        if fi.get("in_library"):
            badge = Gtk.Label(label=_("✓ In library"))
            badge.add_css_class("caption")
            badge.add_css_class("success")
            outer.append(badge)

        self.set_child(outer)

    @property
    def fi(self) -> dict:
        return self._fi

    @property
    def checked(self) -> bool:
        return self._check.get_active()

    def set_checked(self, v: bool):
        self._check.set_active(v)


# ── KoReader book row ─────────────────────────────────────────────────────────

class _KrRow(Gtk.ListBoxRow):
    def __init__(self, book: dict):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_lbl = Gtk.Label(label=book.get("title") or "")
        title_lbl.set_xalign(0)
        title_lbl.set_hexpand(True)
        title_lbl.set_ellipsize(3)
        top.append(title_lbl)

        pct = book.get("pct", 0)
        pct_lbl = Gtk.Label(label=f"{pct}%")
        pct_lbl.add_css_class("caption")
        pct_lbl.add_css_class("dim-label")
        top.append(pct_lbl)
        box.append(top)

        if book.get("authors"):
            auth_lbl = Gtk.Label(label=book["authors"])
            auth_lbl.set_xalign(0)
            auth_lbl.set_ellipsize(3)
            auth_lbl.add_css_class("dim-label")
            auth_lbl.add_css_class("caption")
            box.append(auth_lbl)

        bar = Gtk.ProgressBar()
        bar.set_fraction(pct / 100.0)
        box.append(bar)

        meta2_parts = []
        if book.get("read_time_min"):
            meta2_parts.append(_fmt_minutes(book["read_time_min"]))
        if book.get("highlights"):
            meta2_parts.append(ngettext("{n} highlight", "{n} highlights",
                                        book["highlights"]).format(n=book["highlights"]))
        if book.get("last_open"):
            meta2_parts.append(_days_ago(book["last_open"]))
        if book.get("eta_min"):
            meta2_parts.append(_("ETA {t}").format(t=_fmt_minutes(book["eta_min"])))

        if meta2_parts:
            meta_lbl = Gtk.Label(label="  ·  ".join(meta2_parts))
            meta_lbl.set_xalign(0)
            meta_lbl.add_css_class("dim-label")
            meta_lbl.add_css_class("caption")
            box.append(meta_lbl)

        self.set_child(box)


# ── Stat card ─────────────────────────────────────────────────────────────────

def _make_stat_card(heading: str, value: str) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.add_css_class("card")
    card.set_hexpand(True)
    card.set_margin_start(4)
    card.set_margin_end(4)
    card.set_margin_top(4)
    card.set_margin_bottom(4)
    card.set_halign(Gtk.Align.FILL)

    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    inner.set_margin_start(12)
    inner.set_margin_end(12)
    inner.set_margin_top(10)
    inner.set_margin_bottom(10)

    val_lbl = Gtk.Label(label=value)
    val_lbl.add_css_class("title-2")
    val_lbl.set_xalign(0)
    inner.append(val_lbl)

    hdg_lbl = Gtk.Label(label=heading)
    hdg_lbl.add_css_class("dim-label")
    hdg_lbl.add_css_class("caption")
    hdg_lbl.set_xalign(0)
    inner.append(hdg_lbl)

    card.append(inner)
    return card


# ── Import dialog ─────────────────────────────────────────────────────────────

class _ImportFromDeviceDialog(Gtk.Dialog):
    def __init__(self, parent, files: list[dict]):
        super().__init__(title=_("Importing from device"), transient_for=parent, modal=True)
        self.set_default_size(420, 140)
        self.set_resizable(False)
        self._files = files
        self._imported = 0
        self._errors = 0

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_bottom(16)

        self._lbl = Gtk.Label(label=_("Preparing…"))
        self._lbl.set_xalign(0)
        self._lbl.set_ellipsize(3)
        box.append(self._lbl)

        self._bar = Gtk.ProgressBar()
        box.append(self._bar)

        self._close_btn = self.add_button(_("Close"), Gtk.ResponseType.OK)
        self._close_btn.set_sensitive(False)

        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        total = len(self._files)
        for i, fi in enumerate(self._files):
            GLib.idle_add(self._update, i, total, fi.get("name", ""))
            try:
                proto = fi.get("proto", "cable")
                if proto == "cable":
                    result = import_epub(Path(fi["path"]), action="copy")
                    if result:
                        self._imported += 1
                    else:
                        self._errors += 1
                else:
                    data, err = download_device_file(fi)
                    if data:
                        suffix = Path(fi.get("name", "book.epub")).suffix or ".epub"
                        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                            tmp.write(data)
                            tmp_path = Path(tmp.name)
                        try:
                            result = import_epub(tmp_path, action="copy")
                            if result:
                                self._imported += 1
                            else:
                                self._errors += 1
                        finally:
                            try:
                                tmp_path.unlink()
                            except Exception:
                                pass
                    else:
                        self._errors += 1
            except Exception:
                self._errors += 1
        GLib.idle_add(self._done, total)

    def _update(self, i, total, name):
        self._lbl.set_label(_("Importing: {name}").format(name=name))
        self._bar.set_fraction((i + 1) / total if total else 1.0)

    def _done(self, total):
        msg = ngettext("{n} book added", "{n} books added", self._imported).format(n=self._imported)
        if self._errors:
            msg += "  ·  " + ngettext("{n} error", "{n} errors", self._errors).format(n=self._errors)
        self._lbl.set_label(msg)
        self._bar.set_fraction(1.0)
        self._close_btn.set_sensitive(True)


# ── Device page ───────────────────────────────────────────────────────────────

class DevicePage(Gtk.Box):
    def __init__(self, on_back=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_back_cb = on_back
        self._device = None
        self._all_files: list[dict] = []
        self._filter_not_in_lib = False
        self._sort_mode = 0
        self._kr_loaded = False
        self._build_ui()

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(8)
        header.set_margin_end(12)
        header.set_margin_top(6)
        header.set_margin_bottom(6)

        back_btn = Gtk.Button()
        back_btn.set_icon_name("go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.connect("clicked", self._on_back)
        header.append(back_btn)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.set_hexpand(True)
        title_box.set_valign(Gtk.Align.CENTER)

        self._title_lbl = Gtk.Label(label="")
        self._title_lbl.set_xalign(0)
        self._title_lbl.add_css_class("title-4")
        title_box.append(self._title_lbl)

        self._conn_lbl = Gtk.Label(label="")
        self._conn_lbl.set_xalign(0)
        self._conn_lbl.add_css_class("dim-label")
        self._conn_lbl.add_css_class("caption")
        title_box.append(self._conn_lbl)

        header.append(title_box)
        self.append(header)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Tab bar ───────────────────────────────────────────────────────
        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tab_bar.add_css_class("linked")
        tab_bar.set_margin_start(12)
        tab_bar.set_margin_end(12)
        tab_bar.set_margin_top(8)
        tab_bar.set_margin_bottom(8)
        tab_bar.set_hexpand(True)

        self._tab_lib = Gtk.ToggleButton(label=_("Library"))
        self._tab_lib.set_hexpand(True)
        self._tab_lib.set_active(True)
        tab_bar.append(self._tab_lib)

        self._tab_kr = Gtk.ToggleButton(label=_("KoReader"))
        self._tab_kr.set_hexpand(True)
        self._tab_kr.set_group(self._tab_lib)
        tab_bar.append(self._tab_kr)

        self._tab_lib.connect("toggled", self._on_tab_toggled)
        self._tab_kr.connect("toggled", self._on_tab_kr_toggled)

        self.append(tab_bar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Inner stack ───────────────────────────────────────────────────
        self._inner_stack = Gtk.Stack()
        self._inner_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._inner_stack.set_transition_duration(120)
        self._inner_stack.set_vexpand(True)
        self.append(self._inner_stack)

        self._inner_stack.add_named(self._build_lib_page(), "lib")
        self._inner_stack.add_named(self._build_kr_page(), "kr")

    # ── Library tab UI ────────────────────────────────────────────────────────

    def _build_lib_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Toolbar row 1
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(4)

        self._lib_spinner = Gtk.Spinner()
        toolbar.append(self._lib_spinner)

        self._lib_status_lbl = Gtk.Label(label="")
        self._lib_status_lbl.set_xalign(0)
        self._lib_status_lbl.set_hexpand(True)
        self._lib_status_lbl.add_css_class("dim-label")
        self._lib_status_lbl.add_css_class("caption")
        toolbar.append(self._lib_status_lbl)

        self._lib_count_lbl = Gtk.Label(label="")
        self._lib_count_lbl.add_css_class("dim-label")
        self._lib_count_lbl.add_css_class("caption")
        toolbar.append(self._lib_count_lbl)

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("circular")
        refresh_btn.add_css_class("flat")
        refresh_btn.connect("clicked", self._on_refresh_clicked)
        toolbar.append(refresh_btn)

        self._import_btn = Gtk.Button(label=_("Add to library"))
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.set_visible(False)
        self._import_btn.connect("clicked", self._on_import_clicked)
        toolbar.append(self._import_btn)

        self._delete_btn = Gtk.Button(label=_("Delete selected"))
        self._delete_btn.add_css_class("destructive-action")
        self._delete_btn.set_visible(False)
        self._delete_btn.connect("clicked", self._on_delete_clicked)
        toolbar.append(self._delete_btn)

        page.append(toolbar)

        # Toolbar row 2: search + filter + sort
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctrl.set_margin_start(12)
        ctrl.set_margin_end(12)
        ctrl.set_margin_top(4)
        ctrl.set_margin_bottom(8)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(_("Search files…"))
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        ctrl.append(self._search_entry)

        self._filter_btn = Gtk.ToggleButton(label=_("Not in library"))
        self._filter_btn.add_css_class("pill")
        self._filter_btn.connect("toggled", self._on_filter_toggled)
        ctrl.append(self._filter_btn)

        sort_lbl = Gtk.Label(label=_("Sort:"))
        sort_lbl.add_css_class("dim-label")
        ctrl.append(sort_lbl)

        self._sort_dd = Gtk.DropDown.new_from_strings([_("Name"), _("Size"), _("Recent")])
        self._sort_dd.set_selected(0)
        self._sort_dd.connect("notify::selected", self._on_sort_changed)
        ctrl.append(self._sort_dd)

        page.append(ctrl)
        page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self._file_list = Gtk.ListBox()
        self._file_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._file_list.add_css_class("boxed-list")
        self._file_list.set_margin_top(12)
        self._file_list.set_margin_start(12)
        self._file_list.set_margin_end(12)
        self._file_list.set_margin_bottom(12)
        scroll.set_child(self._file_list)
        page.append(scroll)

        return page

    # ── KoReader tab UI ───────────────────────────────────────────────────────

    def _build_kr_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_margin_top(24)
        spinner_box.set_margin_bottom(16)

        self._kr_spinner = Gtk.Spinner()
        self._kr_spinner.set_size_request(32, 32)
        spinner_box.append(self._kr_spinner)

        self._kr_status_lbl = Gtk.Label(label="")
        self._kr_status_lbl.add_css_class("dim-label")
        self._kr_status_lbl.add_css_class("caption")
        spinner_box.append(self._kr_status_lbl)

        page.append(spinner_box)

        # Stats cards row
        self._stats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._stats_row.set_margin_start(12)
        self._stats_row.set_margin_end(12)
        self._stats_row.set_margin_bottom(8)
        self._stats_row.set_visible(False)
        page.append(self._stats_row)

        self._card_streak = _make_stat_card(_("Reading streak"), "–")
        self._stats_row.append(self._card_streak)
        self._card_speed = _make_stat_card(_("Avg speed"), "–")
        self._stats_row.append(self._card_speed)
        self._card_hour = _make_stat_card(_("Best hour"), "–")
        self._stats_row.append(self._card_hour)

        # Recent reads heading
        self._kr_heading = Gtk.Label(label=_("Recently read"))
        self._kr_heading.add_css_class("title-4")
        self._kr_heading.set_xalign(0)
        self._kr_heading.set_margin_start(16)
        self._kr_heading.set_margin_top(4)
        self._kr_heading.set_margin_bottom(8)
        self._kr_heading.set_visible(False)
        page.append(self._kr_heading)

        kr_scroll = Gtk.ScrolledWindow()
        kr_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        kr_scroll.set_vexpand(True)

        self._kr_list = Gtk.ListBox()
        self._kr_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._kr_list.add_css_class("boxed-list")
        self._kr_list.set_margin_start(12)
        self._kr_list.set_margin_end(12)
        self._kr_list.set_margin_bottom(12)
        kr_scroll.set_child(self._kr_list)
        page.append(kr_scroll)

        return page

    # ── Public API ────────────────────────────────────────────────────────────

    def open_device(self, device: dict):
        self._device = device
        self._kr_loaded = False
        self._title_lbl.set_label(device.get("name", _("Device")))

        conn_parts = []
        if device.get("path"):
            conn_parts.append(_("cable"))
        if device.get("path_wifi"):
            conn_parts.append(_("WiFi"))
        if device.get("path_sftp"):
            conn_parts.append(_("SFTP"))
        self._conn_lbl.set_label(", ".join(conn_parts) if conn_parts else "")

        self._reset_lib_tab()
        self._reset_kr_tab()
        self._tab_lib.set_active(True)
        self._inner_stack.set_visible_child_name("lib")
        self._load_files()

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _on_tab_toggled(self, btn):
        if btn.get_active():
            self._inner_stack.set_visible_child_name("lib")

    def _on_tab_kr_toggled(self, btn):
        if btn.get_active():
            self._inner_stack.set_visible_child_name("kr")
            if not self._kr_loaded:
                self._load_kr()

    # ── Reset helpers ─────────────────────────────────────────────────────────

    def _reset_lib_tab(self):
        self._all_files = []
        self._filter_not_in_lib = False
        self._sort_mode = 0
        self._filter_btn.set_active(False)
        self._sort_dd.set_selected(0)
        self._search_entry.set_text("")
        self._lib_status_lbl.set_label("")
        self._lib_count_lbl.set_label("")
        self._import_btn.set_visible(False)
        self._delete_btn.set_visible(False)
        while self._file_list.get_first_child():
            self._file_list.remove(self._file_list.get_first_child())

    def _reset_kr_tab(self):
        self._kr_loaded = False
        self._kr_status_lbl.set_label("")
        self._stats_row.set_visible(False)
        self._kr_heading.set_visible(False)
        while self._kr_list.get_first_child():
            self._kr_list.remove(self._kr_list.get_first_child())

    # ── Back ──────────────────────────────────────────────────────────────────

    def _on_back(self, _btn):
        if self._on_back_cb:
            self._on_back_cb()

    # ── File loading ──────────────────────────────────────────────────────────

    def _on_refresh_clicked(self, _btn):
        self._reset_lib_tab()
        self._load_files()

    def _load_files(self):
        if not self._device:
            return
        self._lib_spinner.start()
        self._lib_status_lbl.set_label(_("Loading files…"))
        device = self._device
        threading.Thread(target=self._bg_load_files, args=(device,), daemon=True).start()

    def _bg_load_files(self, device: dict):
        files, err = list_device_files(device)
        if err and not files:
            GLib.idle_add(self._on_files_error, err)
            return

        lib_books = get_all_books()
        lib_norms = {_norm(b["title"]) for b in lib_books}

        for fi in files:
            stem = Path(fi.get("name", "")).stem.replace("_", " ")
            fi["in_library"] = _norm(stem) in lib_norms

        GLib.idle_add(self._on_files_loaded, files, err)

    def _on_files_error(self, err: str):
        self._lib_spinner.stop()
        self._lib_status_lbl.set_label(_("Error: {msg}").format(msg=err))

    def _on_files_loaded(self, files: list, err: str | None):
        self._lib_spinner.stop()
        self._all_files = files
        if err:
            self._lib_status_lbl.set_label(_("Partial: {msg}").format(msg=err))
        else:
            self._lib_status_lbl.set_label("")
        self._apply_filters()

    def _apply_filters(self):
        query = self._search_entry.get_text().lower()
        shown = self._all_files

        if self._filter_not_in_lib:
            shown = [f for f in shown if not f.get("in_library")]

        if query:
            shown = [f for f in shown if query in f.get("name", "").lower()]

        mode = self._sort_dd.get_selected()
        if mode == 0:
            shown = sorted(shown, key=lambda f: f.get("name", "").lower())
        elif mode == 1:
            shown = sorted(shown, key=lambda f: f.get("size", 0), reverse=True)
        elif mode == 2:
            shown = sorted(shown, key=lambda f: f.get("mtime", 0), reverse=True)

        while self._file_list.get_first_child():
            self._file_list.remove(self._file_list.get_first_child())

        for fi in shown:
            row = _FileRow(fi)
            row._check = row._check
            row._check.connect("toggled", self._on_check_toggled)
            self._file_list.append(row)

        n = len(shown)
        self._lib_count_lbl.set_label(ngettext("{n} file", "{n} files", n).format(n=n))
        self._update_action_buttons()

    def _on_check_toggled(self, _btn):
        self._update_action_buttons()

    def _update_action_buttons(self):
        selected = self._get_checked_files()
        self._import_btn.set_visible(len(selected) > 0)
        self._delete_btn.set_visible(len(selected) > 0)

    def _get_checked_files(self) -> list[dict]:
        result = []
        row = self._file_list.get_first_child()
        while row:
            if isinstance(row, _FileRow) and row.checked:
                result.append(row.fi)
            row = row.get_next_sibling()
        return result

    def _on_search_changed(self, _entry):
        self._apply_filters()

    def _on_filter_toggled(self, btn):
        self._filter_not_in_lib = btn.get_active()
        self._apply_filters()

    def _on_sort_changed(self, _dd, _param):
        self._apply_filters()

    def _on_import_clicked(self, _btn):
        selected = self._get_checked_files()
        if not selected:
            return
        dlg = _ImportFromDeviceDialog(self.get_root(), selected)
        dlg.connect("response", self._on_import_done)
        dlg.show()

    def _on_import_done(self, dlg, _response):
        dlg.destroy()
        self._load_files()

    def _on_delete_clicked(self, _btn):
        selected = self._get_checked_files()
        if not selected:
            return
        n = len(selected)
        confirm = Gtk.MessageDialog(
            transient_for=self.get_root(),
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=ngettext(
                "Delete {n} file from device?",
                "Delete {n} files from device?",
                n,
            ).format(n=n),
        )
        confirm.add_button(_("Delete"), Gtk.ResponseType.ACCEPT)
        confirm.get_widget_for_response(Gtk.ResponseType.ACCEPT).add_css_class("destructive-action")
        confirm.connect("response", self._on_delete_confirmed, selected)
        confirm.show()

    def _on_delete_confirmed(self, dlg, response, selected):
        dlg.destroy()
        if response != Gtk.ResponseType.ACCEPT:
            return
        device = self._device
        def _bg():
            for fi in selected:
                delete_device_file(device, fi)
            GLib.idle_add(self._load_files)
        threading.Thread(target=_bg, daemon=True).start()

    # ── KoReader loading ──────────────────────────────────────────────────────

    def _load_kr(self):
        if not self._device:
            return
        self._kr_spinner.start()
        self._kr_status_lbl.set_label(_("Downloading statistics…"))
        device = self._device
        threading.Thread(target=self._bg_load_kr, args=(device,), daemon=True).start()

    def _bg_load_kr(self, device: dict):
        data, err = get_koreader_db_bytes(device)
        if not data:
            GLib.idle_add(self._on_kr_error, err or _("KoReader database not found"))
            return

        try:
            with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            conn = sqlite3.connect(tmp_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cur.fetchall()}

            ps_table = None
            if "page_stat_data" in tables:
                ps_table = "page_stat_data"
            elif "page_stat" in tables:
                ps_table = "page_stat"

            if "book" not in tables or ps_table is None:
                conn.close()
                GLib.idle_add(self._on_kr_error, _("Incompatible KoReader database"))
                return

            cur.execute(f"""
                SELECT b.id, b.title, b.authors, b.pages, b.total_read_time, b.last_open,
                       b.total_read_pages, b.highlights,
                       (SELECT MAX(ps.page) FROM {ps_table} ps WHERE ps.id_book = b.id) AS max_page
                FROM book b
                WHERE b.last_open IS NOT NULL AND b.pages > 0
                ORDER BY b.last_open DESC
                LIMIT 50
            """)
            rows = cur.fetchall()

            books = []
            for r in rows:
                pages = r["pages"] or 1
                max_page = r["max_page"] or 0
                pct = min(100, int((max_page + 1) / pages * 100))
                total_time_sec = r["total_read_time"] or 0
                read_pages = r["total_read_pages"] or 1
                speed_ppm = (read_pages / (total_time_sec / 60.0)) if total_time_sec > 0 else 0
                remaining = max(0, pages - max_page - 1)
                eta_min = int(remaining / speed_ppm) if speed_ppm > 0 else 0
                books.append({
                    "id": r["id"],
                    "title": r["title"] or "",
                    "authors": r["authors"] or "",
                    "pages": pages,
                    "pct": pct,
                    "read_time_min": int(total_time_sec / 60),
                    "last_open": r["last_open"] or 0,
                    "highlights": r["highlights"] or 0,
                    "eta_min": eta_min,
                })

            # Daily activity for streak (last 60 days)
            cutoff = int((datetime.now() - timedelta(days=60)).timestamp())
            try:
                cur.execute(f"""
                    SELECT date(datetime(start_time, 'unixepoch', 'localtime')) AS day,
                           SUM(duration) AS secs
                    FROM {ps_table}
                    WHERE start_time >= ?
                    GROUP BY day
                    ORDER BY day DESC
                """, (cutoff,))
                active_days = {r2[0] for r2 in cur.fetchall()}
            except Exception:
                try:
                    cur.execute(f"""
                        SELECT date(datetime(period, 'unixepoch', 'localtime')) AS day
                        FROM {ps_table}
                        WHERE period >= ?
                        GROUP BY day
                        ORDER BY day DESC
                    """, (cutoff,))
                    active_days = {r2[0] for r2 in cur.fetchall()}
                except Exception:
                    active_days = set()

            streak = 0
            check = date.today()
            while check.isoformat() in active_days:
                streak += 1
                check -= timedelta(days=1)

            # Best hour of day
            try:
                cur.execute(f"""
                    SELECT strftime('%H', datetime(start_time, 'unixepoch', 'localtime')) AS hr,
                           SUM(duration) AS total
                    FROM {ps_table}
                    GROUP BY hr
                    ORDER BY total DESC
                    LIMIT 1
                """)
                hr_row = cur.fetchone()
                best_hour = f"{int(hr_row[0]):02d}:00" if hr_row else "–"
            except Exception:
                best_hour = "–"

            # Global speed
            cur.execute("SELECT SUM(total_read_time), SUM(total_read_pages) FROM book")
            speed_row = cur.fetchone()
            if speed_row and speed_row[0] and speed_row[1] and speed_row[0] > 0:
                ppm = speed_row[1] / (speed_row[0] / 60.0)
                speed_str = _("{n} p/h").format(n=int(ppm * 60))
            else:
                speed_str = "–"

            conn.close()
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

            GLib.idle_add(self._on_kr_loaded, books, streak, speed_str, best_hour)

        except Exception as exc:
            GLib.idle_add(self._on_kr_error, str(exc))

    def _on_kr_error(self, msg: str):
        self._kr_spinner.stop()
        self._kr_status_lbl.set_label(_("Error: {msg}").format(msg=msg))

    def _on_kr_loaded(self, books: list, streak: int, speed_str: str, best_hour: str):
        self._kr_spinner.stop()
        self._kr_status_lbl.set_label("")
        self._kr_loaded = True

        streak_label = ngettext("{n} day", "{n} days", streak).format(n=streak)
        for card, (heading, value) in zip(
            [self._card_streak, self._card_speed, self._card_hour],
            [
                (_("Reading streak"), streak_label),
                (_("Avg speed"), speed_str),
                (_("Best hour"), best_hour),
            ],
        ):
            inner = card.get_first_child()
            val_lbl = inner.get_first_child()
            hdg_lbl = val_lbl.get_next_sibling()
            val_lbl.set_label(value)
            hdg_lbl.set_label(heading)

        self._stats_row.set_visible(True)
        self._kr_heading.set_visible(True)

        while self._kr_list.get_first_child():
            self._kr_list.remove(self._kr_list.get_first_child())

        for b in books:
            self._kr_list.append(_KrRow(b))
