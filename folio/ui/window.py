"""
Main application window.
"""

import json
import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk, Gio

from folio.database import (
    get_all_books, search_books, count_books,
    get_books_by_author, get_books_by_series,
    get_profiles, create_profile, delete_profile,
    get_or_create_default_profile,
)
from folio.paths import COVERS_DIR, PREFS_FILE
from folio.scanner import import_epub

from folio.ui.book_detail import BookDetail
from folio.ui.device_page import DevicePage
from folio.ui.discover import DiscoverPage
from folio.ui.edit_books import EditPage
from folio.ui.reading import ReadingPage
from folio.ui.settings import SettingsPage
from folio.ui.stats import StatsPage
from folio.devices import connected_devices

def _load_prefs() -> dict:
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(prefs))
    except Exception:
        pass


CARD_W = 150
CARD_H = 225
THUMB_W, THUMB_H = 48, 72

_SIDEBAR_CSS = """
#folio-sidebar {
    background-color: mix(@window_bg_color, @window_fg_color, 0.05);
    border-right: 1px solid mix(@window_bg_color, @window_fg_color, 0.12);
}
"""

_SIDEBAR_TABS = [
    ("library",  "Library",   "view-grid-symbolic",          True),
    ("reading",  "Reading",   "bookmark-symbolic",           True),
    ("stats",    "Statistics","office-chart-bar-symbolic",   True),
    ("edit",     "Edit",      "document-edit-symbolic",      True),
    ("discover", "Discover",  "starred-symbolic",            True),
    ("settings", "Settings",  "preferences-system-symbolic", True),
]

_SORT_LABELS = ["Recent", "Title A–Z", "Author", "Series", "Year"]
_SORT_KEYS   = ["recientes", "titulo",  "autor",  "serie",  "anyo"]


def _load_cover_pixbuf(book_id: int, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    path = COVERS_DIR / f"{book_id}.webp"
    if path.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), w, h)
        except Exception:
            pass
    return None


def _placeholder_pixbuf(w: int, h: int) -> GdkPixbuf.Pixbuf:
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(0x2d2d2dff)
    return pb


# ── Book card (grid view) ─────────────────────────────────────────────────────

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
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder_pixbuf(CARD_W, CARD_H)))
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
            pb = _load_cover_pixbuf(book_id, CARD_W, CARD_H)
            if pb:
                GLib.idle_add(self._set_cover, pb)
        threading.Thread(target=_bg, daemon=True).start()

    def _set_cover(self, pb):
        self._cover.set_paintable(Gdk.Texture.new_for_pixbuf(pb))

    @property
    def book(self):
        return self._book


# ── Book row (list view) ──────────────────────────────────────────────────────

class BookListRow(Gtk.ListBoxRow):
    def __init__(self, book: dict):
        super().__init__()
        self._book = book

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self._thumb = Gtk.Picture()
        self._thumb.set_size_request(THUMB_W, THUMB_H)
        self._thumb.set_content_fit(Gtk.ContentFit.COVER)
        self._thumb.set_can_shrink(False)
        self._thumb.set_paintable(Gdk.Texture.new_for_pixbuf(_placeholder_pixbuf(THUMB_W, THUMB_H)))
        box.append(self._thumb)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        txt.set_hexpand(True)
        txt.set_valign(Gtk.Align.CENTER)

        title_lbl = Gtk.Label(label=book["title"])
        title_lbl.set_xalign(0)
        title_lbl.set_ellipsize(3)
        txt.append(title_lbl)

        if book.get("author"):
            lbl = Gtk.Label(label=book["author"])
            lbl.add_css_class("dim-label")
            lbl.add_css_class("caption")
            lbl.set_xalign(0)
            lbl.set_ellipsize(3)
            txt.append(lbl)

        meta_parts = []
        if book.get("series_name"):
            s = book["series_name"]
            if book.get("series_num"):
                s += "  ·  " + _("vol. {num}").format(num=book["series_num"])
            meta_parts.append(s)
        if book.get("year"):
            meta_parts.append(str(book["year"]))
        if meta_parts:
            lbl = Gtk.Label(label="  ·  ".join(meta_parts))
            lbl.add_css_class("dim-label")
            lbl.add_css_class("caption")
            lbl.set_xalign(0)
            lbl.set_ellipsize(3)
            txt.append(lbl)

        box.append(txt)
        self.set_child(box)

    def load_cover_async(self):
        book_id = self._book["id"]
        def _bg():
            pb = _load_cover_pixbuf(book_id, THUMB_W, THUMB_H)
            if pb:
                GLib.idle_add(self._set_thumb, pb)
        threading.Thread(target=_bg, daemon=True).start()

    def _set_thumb(self, pb):
        self._thumb.set_paintable(Gdk.Texture.new_for_pixbuf(pb))

    @property
    def book(self):
        return self._book


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Folio")
        self.set_default_size(1060, 660)
        self._active_root = "library"
        self._detail_came_from = "grid"
        self._sort = "recientes"
        self._view_mode = "grid"
        self._active_profile_id: int = get_or_create_default_profile()
        self._apply_css()
        self._build_ui()
        self._refresh_sidebar_devices()
        self._load_books()
        self._vol_monitor = Gio.VolumeMonitor.get()
        self._vol_monitor.connect("mount-added",   lambda _m, _v: GLib.idle_add(self._refresh_sidebar_devices))
        self._vol_monitor.connect("mount-removed",  lambda _m, _v: GLib.idle_add(self._refresh_sidebar_devices))

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
        self._back_btn.set_tooltip_text(_("Back"))
        self._back_btn.connect("clicked", self._on_back)
        self._back_btn.set_visible(False)
        header.pack_start(self._back_btn)

        import_btn = Gtk.MenuButton()
        import_btn.set_icon_name("list-add-symbolic")
        import_btn.set_tooltip_text(_("Add books"))
        self._import_popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover_box.set_margin_top(4); popover_box.set_margin_bottom(4)
        popover_box.set_margin_start(4); popover_box.set_margin_end(4)
        files_btn = Gtk.Button(label=_("Add EPUB files…"))
        files_btn.add_css_class("flat")
        files_btn.connect("clicked", self._on_import_files_clicked)
        popover_box.append(files_btn)
        folder_btn = Gtk.Button(label=_("Add from folder…"))
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", self._on_import_folder_clicked)
        popover_box.append(folder_btn)
        self._import_popover.set_child(popover_box)
        import_btn.set_popover(self._import_popover)
        header.pack_start(import_btn)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search…"))
        self._search.set_size_request(220, -1)
        self._search.connect("search-changed", self._on_search_changed)
        header.pack_end(self._search)

        self._count_lbl = Gtk.Label()
        self._count_lbl.add_css_class("dim-label")
        self._count_lbl.add_css_class("caption")
        header.pack_end(self._count_lbl)

        # Profile switcher
        self._profile_btn = Gtk.MenuButton()
        self._profile_btn.set_icon_name("person-symbolic")
        self._profile_btn.add_css_class("flat")
        self._profile_btn.set_tooltip_text(_("Switch profile"))
        self._profile_popover = Gtk.Popover()
        self._profile_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._profile_pop_box.set_margin_top(4); self._profile_pop_box.set_margin_bottom(4)
        self._profile_pop_box.set_margin_start(4); self._profile_pop_box.set_margin_end(4)
        self._profile_popover.set_child(self._profile_pop_box)
        self._profile_btn.set_popover(self._profile_popover)
        header.pack_end(self._profile_btn)
        self._rebuild_profile_menu()

        # ── Paned: sidebar + stack ─────────────────────────────────────────
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_position(170)
        self.set_child(paned)

        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.add_controller(drop_target)

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
            row_box.set_margin_start(10); row_box.set_margin_end(10)
            row_box.set_margin_top(9);   row_box.set_margin_bottom(9)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(16)
            row_box.append(img)
            lbl = Gtk.Label(label=_(label))
            lbl.set_xalign(0); lbl.set_hexpand(True)
            row_box.append(lbl)
            row = Gtk.ListBoxRow()
            row.set_child(row_box)
            row.set_sensitive(enabled)
            self._sidebar_list.append(row)
            self._sidebar_rows[key] = row
        sidebar_box.append(self._sidebar_list)

        sidebar_box.append(Gtk.Separator())
        self._sidebar_devices_lb = Gtk.ListBox()
        self._sidebar_devices_lb.set_selection_mode(Gtk.SelectionMode.NONE)
        self._sidebar_devices_lb.add_css_class("navigation-sidebar")
        self._sidebar_devices_lb.connect("row-activated", self._on_device_row_activated)
        sidebar_box.append(self._sidebar_devices_lb)

        paned.set_start_child(sidebar_box)

        # ── Main stack ────────────────────────────────────────────────────
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        self._stack.set_transition_duration(180)
        paned.set_end_child(self._stack)
        self._stack.connect("notify::visible-child-name", self._on_stack_page_changed)

        # ── Library page ──────────────────────────────────────────────────
        lib_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._stack.add_named(lib_box, "grid")

        # Toolbar: sort dropdown + view toggle
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(12); toolbar.set_margin_end(12)
        toolbar.set_margin_top(8);   toolbar.set_margin_bottom(8)

        sort_lbl = Gtk.Label(label=_("Sort:"))
        sort_lbl.add_css_class("dim-label")
        toolbar.append(sort_lbl)

        self._sort_dd = Gtk.DropDown.new_from_strings([_(_l) for _l in _SORT_LABELS])
        self._sort_dd.set_selected(0)
        self._sort_dd.connect("notify::selected", self._on_sort_changed)
        toolbar.append(self._sort_dd)

        spacer = Gtk.Box(); spacer.set_hexpand(True)
        toolbar.append(spacer)

        view_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        view_box.add_css_class("linked")
        self._btn_grid_view = Gtk.ToggleButton()
        self._btn_grid_view.set_icon_name("view-grid-symbolic")
        self._btn_grid_view.set_tooltip_text(_("Grid view"))
        self._btn_grid_view.set_active(True)
        self._btn_grid_view.connect("toggled", self._on_view_toggled, "grid")
        view_box.append(self._btn_grid_view)
        self._btn_list_view = Gtk.ToggleButton()
        self._btn_list_view.set_icon_name("view-list-symbolic")
        self._btn_list_view.set_tooltip_text(_("List view"))
        self._btn_list_view.connect("toggled", self._on_view_toggled, "list")
        view_box.append(self._btn_list_view)
        toolbar.append(view_box)

        lib_box.append(toolbar)
        lib_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # View stack (grid / list)
        self._view_stack = Gtk.Stack()
        self._view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._view_stack.set_transition_duration(120)
        self._view_stack.set_vexpand(True)
        lib_box.append(self._view_stack)

        # Grid (FlowBox)
        grid_scroll = Gtk.ScrolledWindow()
        grid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._flow = Gtk.FlowBox()
        self._flow.set_valign(Gtk.Align.START)
        self._flow.set_max_children_per_line(12)
        self._flow.set_min_children_per_line(2)
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_margin_top(12)
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.connect("child-activated", self._on_card_activated)
        grid_scroll.set_child(self._flow)
        self._view_stack.add_named(grid_scroll, "grid")

        # List (ListBox)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._lib_list = Gtk.ListBox()
        self._lib_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._lib_list.set_margin_top(8)
        self._lib_list.set_margin_bottom(16)
        self._lib_list.set_margin_start(16)
        self._lib_list.set_margin_end(16)
        self._lib_list.connect("row-activated", self._on_list_row_activated)
        list_scroll.set_child(self._lib_list)
        self._view_stack.add_named(list_scroll, "list")

        # ── Book detail ───────────────────────────────────────────────────
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._detail = BookDetail(
            on_open_author=self._open_author_page,
            on_open_series=self._open_series_page,
        )
        detail_scroll.set_child(self._detail)
        self._stack.add_named(detail_scroll, "detail")

        # ── Reading ───────────────────────────────────────────────────────
        self._reading_page = ReadingPage(on_open_book=self._open_book_detail)
        self._stack.add_named(self._reading_page, "reading")

        # ── Edit ──────────────────────────────────────────────────────────
        self._edit_page = EditPage(on_book_deleted=self._on_book_deleted)
        self._stack.add_named(self._edit_page, "edit")

        # ── Statistics ────────────────────────────────────────────────────
        self._stats_page = StatsPage()
        self._stack.add_named(self._stats_page, "stats")

        # ── Discover ──────────────────────────────────────────────────────
        self._discover_page = DiscoverPage(on_open_book=self._open_book_detail)
        self._stack.add_named(self._discover_page, "discover")

        # ── Settings ──────────────────────────────────────────────────────
        self._settings_page = SettingsPage()
        self._stack.add_named(self._settings_page, "settings")

        # ── Device ────────────────────────────────────────────────────────
        self._device_page = DevicePage(on_back=self._close_device_page)
        self._stack.add_named(self._device_page, "device")

        # ── Collection (author / series) ──────────────────────────────────
        coll_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._coll_title = Gtk.Label()
        self._coll_title.add_css_class("title-4")
        self._coll_title.set_margin_top(16); self._coll_title.set_margin_bottom(4)
        self._coll_title.set_margin_start(20); self._coll_title.set_xalign(0)
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
        self._coll_flow.set_margin_start(16); self._coll_flow.set_margin_end(16)
        self._coll_flow.connect("child-activated", self._on_coll_card_activated)
        coll_scroll.set_child(self._coll_flow)
        coll_outer.append(coll_scroll)
        self._stack.add_named(coll_outer, "collection")

        self._sidebar_list.select_row(self._sidebar_rows["library"])

    # ── Sidebar / navigation ──────────────────────────────────────────────────

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
                    self._reading_page.set_profile(self._active_profile_id)
                elif key == "stats":
                    self._stats_page.refresh(self._active_profile_id)
                elif key == "discover":
                    self._discover_page.refresh(self._active_profile_id)
                elif key == "edit":
                    self._edit_page.refresh()
                break

    # ── Profile management ────────────────────────────────────────────────────

    def _rebuild_profile_menu(self):
        while self._profile_pop_box.get_first_child():
            self._profile_pop_box.remove(self._profile_pop_box.get_first_child())

        profiles = get_profiles()
        for p in profiles:
            btn = Gtk.Button(label=("✓ " if p["id"] == self._active_profile_id else "    ") + p["name"])
            btn.add_css_class("flat")
            pid = p["id"]
            btn.connect("clicked", lambda _, _pid=pid: self._switch_profile(_pid))
            self._profile_pop_box.append(btn)

        self._profile_pop_box.append(Gtk.Separator())

        new_btn = Gtk.Button(label=_("+ New profile…"))
        new_btn.add_css_class("flat")
        new_btn.connect("clicked", self._on_new_profile)
        self._profile_pop_box.append(new_btn)

        if len(profiles) > 1:
            del_btn = Gtk.Button(label=_("Delete current profile…"))
            del_btn.add_css_class("flat")
            del_btn.add_css_class("destructive-action")
            del_btn.connect("clicked", self._on_delete_profile)
            self._profile_pop_box.append(del_btn)

    def _switch_profile(self, profile_id: int):
        self._profile_popover.popdown()
        self._active_profile_id = profile_id
        _save_prefs({**_load_prefs(), "active_profile_id": profile_id})
        self._rebuild_profile_menu()
        self._reading_page.set_profile(profile_id)
        if self._stack.get_visible_child_name() == "stats":
            self._stats_page.refresh(profile_id)
        if self._stack.get_visible_child_name() == "discover":
            self._discover_page.refresh(profile_id)

    def _on_new_profile(self, _btn):
        self._profile_popover.popdown()
        dlg = Gtk.Dialog(title=_("New profile"), transient_for=self, modal=True)
        dlg.set_default_size(300, -1)
        box = dlg.get_content_area()
        box.set_margin_top(16); box.set_margin_start(16)
        box.set_margin_end(16); box.set_margin_bottom(8)
        entry = Gtk.Entry()
        entry.set_placeholder_text(_("Profile name"))
        box.append(entry)
        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(_("Create"), Gtk.ResponseType.OK)
        ok.add_css_class("suggested-action")

        def on_resp(d, r):
            if r == Gtk.ResponseType.OK:
                name = entry.get_text().strip()
                if name:
                    pid = create_profile(name)
                    self._switch_profile(pid)
            d.destroy()

        dlg.connect("response", on_resp)
        dlg.show()

    def _on_delete_profile(self, _btn):
        self._profile_popover.popdown()
        profiles = get_profiles()
        if len(profiles) <= 1:
            return
        current_name = next((p["name"] for p in profiles if p["id"] == self._active_profile_id), "")
        confirm = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=_('Delete profile "{name}" and all its reading data?').format(name=current_name),
        )
        confirm.add_button(_("Delete"), Gtk.ResponseType.ACCEPT)
        confirm.get_widget_for_response(Gtk.ResponseType.ACCEPT).add_css_class("destructive-action")

        def on_resp(d, r):
            if r == Gtk.ResponseType.ACCEPT:
                pid_to_del = self._active_profile_id
                remaining = [p for p in profiles if p["id"] != pid_to_del]
                self._switch_profile(remaining[0]["id"])
                delete_profile(pid_to_del)
                self._rebuild_profile_menu()
            d.destroy()

        confirm.connect("response", on_resp)
        confirm.show()

    def _refresh_sidebar_devices(self):
        while self._sidebar_devices_lb.get_first_child():
            self._sidebar_devices_lb.remove(self._sidebar_devices_lb.get_first_child())
        for dev in connected_devices():
            row = Gtk.ListBoxRow()
            row._device = dev
            box = Gtk.Box(spacing=8)
            box.set_margin_start(14); box.set_margin_end(10)
            box.set_margin_top(5); box.set_margin_bottom(5)
            dot = Gtk.Label(label="⬤")
            dot.add_css_class("success")
            lbl = Gtk.Label(label=dev["name"])
            lbl.set_xalign(0); lbl.set_hexpand(True)
            lbl.add_css_class("caption"); lbl.set_ellipsize(3)
            box.append(dot); box.append(lbl)
            row.set_child(box)
            self._sidebar_devices_lb.append(row)

    def _on_device_row_activated(self, lb, row):
        device = getattr(row, "_device", None)
        if device:
            self._open_device_page(device)

    def _open_device_page(self, device: dict):
        self._device_page.open_device(device)
        self._stack.set_visible_child_name("device")
        self._back_btn.set_visible(True)
        self._search.set_visible(False)
        self._count_lbl.set_visible(False)

    def _close_device_page(self):
        self._stack.set_visible_child_name("grid")
        self._back_btn.set_visible(False)
        self._search.set_visible(True)
        self._count_lbl.set_visible(True)
        self._sidebar_list.select_row(self._sidebar_rows["library"])

    def _on_stack_page_changed(self, stack, _param):
        name = stack.get_visible_child_name()
        root = "library" if name in ("grid", "detail", "collection") else name
        if root in self._sidebar_rows:
            self._sidebar_list.select_row(self._sidebar_rows[root])
        self._search.set_visible(name == "grid")
        self._count_lbl.set_visible(name == "grid")

    def _open_book_detail(self, book_id: int):
        self._detail_came_from = self._stack.get_visible_child_name()
        self._detail.load_book(book_id)
        self._stack.set_visible_child_name("detail")
        self._back_btn.set_visible(True)
        self._search.set_visible(False)
        self._count_lbl.set_visible(False)

    def _open_author_page(self, author_name: str):
        self._coll_title.set_label(_("Author: {name}").format(name=author_name))
        self._load_collection(get_books_by_author, author_name)

    def _open_series_page(self, series_name: str):
        self._coll_title.set_label(_("Series: {name}").format(name=series_name))
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
            self._coll_title.get_label() + "  ·  " + ngettext("{n} book", "{n} books", n).format(n=n)
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

    def _on_book_deleted(self, book_id: int):
        self._load_books()

    # ── Library: sort + view mode ─────────────────────────────────────────────

    def _on_sort_changed(self, dd, _):
        self._sort = _SORT_KEYS[dd.get_selected()]
        self._load_books(self._search.get_text())

    def _on_view_toggled(self, btn, mode):
        if not btn.get_active():
            return
        other = self._btn_list_view if mode == "grid" else self._btn_grid_view
        other.set_active(False)
        self._view_mode = mode
        self._view_stack.set_visible_child_name(mode)

    # ── Library: data ─────────────────────────────────────────────────────────

    def _load_books(self, query: str = ""):
        while self._flow.get_first_child():
            self._flow.remove(self._flow.get_first_child())
        while self._lib_list.get_first_child():
            self._lib_list.remove(self._lib_list.get_first_child())

        def _bg():
            books = (search_books(query, sort=self._sort)
                     if query.strip() else get_all_books(sort=self._sort))
            GLib.idle_add(self._populate, books, query)
        threading.Thread(target=_bg, daemon=True).start()

    def _populate(self, books: list, query: str):
        total = count_books()
        self._count_lbl.set_label(
            _("{shown} of {total} books").format(shown=len(books), total=total)
            if query else
            ngettext("{n} book", "{n} books", total).format(n=total)
        )
        for book in books:
            card = BookCard(book)
            self._flow.append(card)
            card.load_cover_async()

            row = BookListRow(book)
            self._lib_list.append(row)
            row.load_cover_async()

    def _on_card_activated(self, flowbox, child):
        card = child.get_child()
        if isinstance(card, BookCard):
            self._open_book_detail(card.book["id"])

    def _on_list_row_activated(self, lb, row):
        if isinstance(row, BookListRow):
            self._open_book_detail(row.book["id"])

    def _on_coll_card_activated(self, flowbox, child):
        card = child.get_child()
        if isinstance(card, BookCard):
            self._open_book_detail(card.book["id"])

    def _on_search_changed(self, entry):
        self._load_books(entry.get_text())

    # ── Import ────────────────────────────────────────────────────────────────

    def _on_import_files_clicked(self, _btn):
        self._import_popover.popdown()
        dialog = Gtk.FileChooserDialog(
            title=_("Select EPUB files"),
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Add"), Gtk.ResponseType.ACCEPT)
        dialog.set_select_multiple(True)
        f = Gtk.FileFilter()
        f.set_name(_("EPUB files (*.epub)"))
        f.add_pattern("*.epub")
        dialog.add_filter(f)
        dialog.connect("response", self._on_import_files_response)
        dialog.show()

    def _on_import_files_response(self, dialog, response):
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        paths = [Path(f.get_path()) for f in dialog.get_files()]
        dialog.destroy()
        if paths:
            self._run_import(paths)

    def _on_import_folder_clicked(self, _btn):
        self._import_popover.popdown()
        dialog = Gtk.FileChooserDialog(
            title=_("Select folder with EPUBs"),
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Scan folder"), Gtk.ResponseType.ACCEPT)
        dialog.connect("response", self._on_import_folder_response)
        dialog.show()

    def _on_import_folder_response(self, dialog, response):
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        folder = Path(dialog.get_file().get_path())
        dialog.destroy()
        self._show_action_dialog(folder=folder)

    def _on_drop(self, _target, value, _x, _y):
        paths = [
            Path(f.get_path()) for f in value.get_files()
            if f.get_path() and f.get_path().lower().endswith(".epub")
        ]
        if paths:
            self._show_action_dialog(paths=paths)
        return bool(paths)

    def _show_action_dialog(self, paths: list | None = None, folder: Path | None = None):
        n = len(paths) if paths else 0
        dlg = _ImportActionDialog(self, n)

        def on_response(d, response):
            action = d.get_action()
            d.destroy()
            if response != Gtk.ResponseType.OK:
                return
            _save_prefs({**_load_prefs(), "import_action": action})
            if paths:
                progress = _ImportProgressDialog(self, paths, action)
            else:
                progress = _FolderImportDialog(self, folder, action)
            progress.connect("response", lambda p, _: (p.destroy(), self._load_books()))
            progress.show()

        dlg.connect("response", on_response)
        dlg.show()

    def _run_import(self, paths: list):
        self._show_action_dialog(paths=paths)


class _ImportActionDialog(Gtk.Dialog):
    def __init__(self, parent, n_files: int):
        super().__init__(title=_("Add books"), transient_for=parent, modal=True)
        self.set_default_size(380, -1)
        self.set_resizable(False)

        last_action = _load_prefs().get("import_action", "copy")

        box = self.get_content_area()
        box.set_spacing(6)
        box.set_margin_top(20); box.set_margin_start(24)
        box.set_margin_end(24); box.set_margin_bottom(16)

        if n_files > 0:
            subtitle = ngettext(
                "What do you want to do with this book?",
                "What do you want to do with these {n} books?",
                n_files,
            ).format(n=n_files)
        else:
            subtitle = _("What do you want to do with the books in this folder?")
        lbl = Gtk.Label(label=subtitle)
        lbl.set_xalign(0)
        lbl.set_margin_bottom(8)
        box.append(lbl)

        self._copy_radio = Gtk.CheckButton(label=_("Copy to library folder"))
        self._copy_radio.set_active(last_action == "copy")
        box.append(self._copy_radio)

        self._move_radio = Gtk.CheckButton(label=_("Move to library folder (removes originals)"))
        self._move_radio.set_group(self._copy_radio)
        self._move_radio.set_active(last_action == "move")
        box.append(self._move_radio)

        self._ref_radio = Gtk.CheckButton(label=_("Just add to library (keep files where they are)"))
        self._ref_radio.set_group(self._copy_radio)
        self._ref_radio.set_active(last_action == "reference")
        box.append(self._ref_radio)

        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        ok_btn = self.add_button(_("Continue"), Gtk.ResponseType.OK)
        ok_btn.add_css_class("suggested-action")

    def get_action(self) -> str:
        if self._move_radio.get_active():
            return "move"
        if self._ref_radio.get_active():
            return "reference"
        return "copy"


class _ImportProgressDialog(Gtk.Dialog):
    def __init__(self, parent, paths: list, action: str = "copy"):
        super().__init__(title=_("Adding books"), transient_for=parent, modal=True)
        self.set_default_size(420, 130)
        self.set_resizable(False)
        self._paths = paths
        self._action = action
        self._imported = 0
        self._skipped = 0

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(20); box.set_margin_start(20)
        box.set_margin_end(20); box.set_margin_bottom(16)

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
        total = len(self._paths)
        for i, path in enumerate(self._paths):
            GLib.idle_add(self._update, i, total, path.stem)
            try:
                result = import_epub(path, action=self._action)
                if result:
                    self._imported += 1
                else:
                    self._skipped += 1
            except Exception:
                self._skipped += 1
        GLib.idle_add(self._done, total)

    def _update(self, i, total, name):
        self._lbl.set_label(_("Importing: {name}").format(name=name))
        self._bar.set_fraction((i + 1) / total)

    def _done(self, total):
        n, s = self._imported, self._skipped
        msg = ngettext("{n} book added", "{n} books added", n).format(n=n)
        if s:
            msg += "  ·  " + ngettext("{n} skipped (already existed)",
                                       "{n} skipped (already existed)", s).format(n=s)
        self._lbl.set_label(msg)
        self._bar.set_fraction(1.0)
        self._close_btn.set_sensitive(True)


class _FolderImportDialog(Gtk.Dialog):
    def __init__(self, parent, folder: Path, action: str = "copy"):
        super().__init__(title=_("Scanning folder"), transient_for=parent, modal=True)
        self.set_default_size(420, 130)
        self.set_resizable(False)
        self._folder = folder
        self._action = action

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(20); box.set_margin_start(20)
        box.set_margin_end(20); box.set_margin_bottom(16)

        self._lbl = Gtk.Label(label=_("Scanning…"))
        self._lbl.set_xalign(0)
        self._lbl.set_ellipsize(3)
        box.append(self._lbl)

        self._bar = Gtk.ProgressBar()
        box.append(self._bar)

        self._close_btn = self.add_button(_("Close"), Gtk.ResponseType.OK)
        self._close_btn.set_sensitive(False)

        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        from folio.scanner import scan_folder
        for current, total, title in scan_folder(self._folder, action=self._action):
            GLib.idle_add(self._update, current, total, title)
        GLib.idle_add(self._done)

    def _update(self, current, total, title):
        self._lbl.set_label(_("Importing: {name}").format(name=title))
        self._bar.set_fraction(current / total if total else 1.0)

    def _done(self):
        self._lbl.set_label(_("Done!"))
        self._bar.set_fraction(1.0)
        self._close_btn.set_sensitive(True)
