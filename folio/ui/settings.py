"""
Settings page — Library, Devices sub-sections.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from folio.devices import (
    load_devices, save_devices, detect_auto_paths, is_connected,
    _wifi_ip, _wifi_folder, _sftp_ip_port,
)
from folio.paths import DATA_DIR


class SettingsPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._build_ui()

    def _build_ui(self):
        self._nav = Gtk.Stack()
        self._nav.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._nav.set_transition_duration(160)
        self._nav.set_vexpand(True)
        self.append(self._nav)

        self._nav.add_named(self._build_menu(),   "menu")
        self._nav.add_named(self._build_library(), "library")
        self._nav.add_named(self._build_devices(), "devices")

    # ── Navigation helpers ────────────────────────────────────────────────

    def _go(self, page: str):
        if page == "devices":
            self._refresh_devices()
        self._nav.set_visible_child_name(page)

    def _detail_header(self, title: str) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row = Gtk.Box(spacing=4)
        row.set_margin_top(4); row.set_margin_bottom(4)
        row.set_margin_start(4); row.set_margin_end(8)
        back_btn = Gtk.Button()
        back_btn.set_icon_name("go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.connect("clicked", lambda _: self._nav.set_visible_child_name("menu"))
        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("title-4")
        row.append(back_btn)
        row.append(title_lbl)
        outer.append(row)
        outer.append(Gtk.Separator())
        return outer

    # ── Menu ──────────────────────────────────────────────────────────────

    def _build_menu(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_margin_top(24); outer.set_margin_bottom(24)
        outer.set_margin_start(24); outer.set_margin_end(24)

        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")

        items = [
            ("library", "drive-harddisk-symbolic",   _("Library"),  _("Data folder and storage")),
            ("devices", "phone-symbolic",             _("Devices"),  _("E-reader configuration")),
            (None,      "mail-send-symbolic",         _("Email"),    _("Coming soon"), False),
        ]
        for item in items:
            page_name, icon_name, label, subtitle = item[0], item[1], item[2], item[3]
            enabled = item[4] if len(item) > 4 else True

            row = Gtk.ListBoxRow()
            row.set_activatable(enabled and page_name is not None)
            row.set_sensitive(enabled)
            box = Gtk.Box(spacing=12)
            box.set_margin_top(10); box.set_margin_bottom(10)
            box.set_margin_start(12); box.set_margin_end(8)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(22)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            txt.set_hexpand(True)
            lbl = Gtk.Label(label=label); lbl.set_xalign(0)
            sub = Gtk.Label(label=subtitle); sub.set_xalign(0)
            sub.add_css_class("dim-label"); sub.add_css_class("caption")
            txt.append(lbl); txt.append(sub)
            chevron = Gtk.Label(label="›"); chevron.add_css_class("dim-label")
            box.append(icon); box.append(txt)
            if enabled:
                box.append(chevron)
            row.set_child(box)
            row._page = page_name
            lb.append(row)

        lb.connect("row-activated", lambda _, row: self._go(row._page) if row._page else None)
        outer.append(lb)
        return outer

    # ── Library ───────────────────────────────────────────────────────────

    def _build_library(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self._detail_header(_("Library")))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(24); content.set_margin_bottom(24)
        content.set_margin_start(24); content.set_margin_end(24)

        lb = Gtk.ListBox()
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")

        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(12); box.set_margin_end(12)
        lbl = Gtk.Label(label=_("Data folder"))
        lbl.set_xalign(0)
        path_lbl = Gtk.Label(label=str(DATA_DIR))
        path_lbl.set_xalign(0)
        path_lbl.add_css_class("dim-label"); path_lbl.add_css_class("caption")
        path_lbl.set_selectable(True)
        path_lbl.set_ellipsize(1)  # START
        box.append(lbl); box.append(path_lbl)
        row.set_child(box)
        lb.append(row)

        content.append(lb)

        note = Gtk.Label(
            label=_("To change the data folder, set the FOLIO_DATA_DIR environment variable before launching.")
        )
        note.add_css_class("dim-label"); note.add_css_class("caption")
        note.set_wrap(True); note.set_xalign(0)
        content.append(note)

        outer.append(content)
        return outer

    # ── Devices ───────────────────────────────────────────────────────────

    def _build_devices(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self._detail_header(_("Devices")))

        # Toolbar: auto-detect + add
        toolbar = Gtk.Box(spacing=8)
        toolbar.set_margin_top(12); toolbar.set_margin_bottom(8)
        toolbar.set_margin_start(16); toolbar.set_margin_end(16)

        detect_btn = Gtk.Button(label=_("Auto-detect"))
        detect_btn.set_icon_name("media-removable-symbolic")
        detect_btn.connect("clicked", self._on_auto_detect)
        toolbar.append(detect_btn)

        spacer = Gtk.Box(); spacer.set_hexpand(True)
        toolbar.append(spacer)

        add_btn = Gtk.Button(label=_("Add device"))
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self._on_add_device)
        toolbar.append(add_btn)

        outer.append(toolbar)
        outer.append(Gtk.Separator())

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self._devices_list = Gtk.ListBox()
        self._devices_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._devices_list.add_css_class("boxed-list")
        self._devices_list.set_margin_top(8); self._devices_list.set_margin_bottom(16)
        self._devices_list.set_margin_start(16); self._devices_list.set_margin_end(16)
        scroll.set_child(self._devices_list)
        outer.append(scroll)

        return outer

    def _refresh_devices(self):
        while self._devices_list.get_first_child():
            self._devices_list.remove(self._devices_list.get_first_child())
        devices = load_devices()
        if not devices:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            lbl = Gtk.Label(label=_("No devices configured yet."))
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(20); lbl.set_margin_bottom(20)
            row.set_child(lbl)
            self._devices_list.append(row)
            return
        for i, dev in enumerate(devices):
            self._devices_list.append(self._make_device_row(dev, i))

    def _make_device_row(self, dev: dict, idx: int) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        box = Gtk.Box(spacing=12)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(12); box.set_margin_end(8)

        connected = is_connected(dev)
        dot = Gtk.Label(label="⬤")
        dot.add_css_class("success" if connected else "dim-label")
        box.append(dot)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        txt.set_hexpand(True)
        name_lbl = Gtk.Label(label=dev["name"]); name_lbl.set_xalign(0)
        txt.append(name_lbl)

        # Connection summary line
        parts = []
        if dev.get("path"):
            parts.append(_("Cable: {p}").format(p=dev["path"]))
        if dev.get("path_wifi"):
            parts.append(_("WiFi: {ip}").format(ip=_wifi_ip(dev)))
        if dev.get("path_sftp"):
            ip, port = _sftp_ip_port(dev)
            parts.append(_("SFTP: {ip}:{port}").format(ip=ip, port=port))
        if parts:
            sub = Gtk.Label(label="  ·  ".join(parts))
            sub.set_xalign(0)
            sub.add_css_class("dim-label"); sub.add_css_class("caption")
            sub.set_ellipsize(3)
            txt.append(sub)

        box.append(txt)

        edit_btn = Gtk.Button()
        edit_btn.set_icon_name("document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.connect("clicked", self._on_edit_device, idx)
        box.append(edit_btn)

        del_btn = Gtk.Button()
        del_btn.set_icon_name("user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.connect("clicked", self._on_delete_device, idx)
        box.append(del_btn)

        row.set_child(box)
        return row

    # ── Device CRUD ───────────────────────────────────────────────────────

    def _on_auto_detect(self, _btn):
        paths = detect_auto_paths()
        if not paths:
            self._show_info(_("No devices detected."))
            return
        devices = load_devices()
        existing = {d.get("path", "") for d in devices}
        added = 0
        for p in paths:
            if str(p) not in existing:
                devices.append({
                    "name": p.name or str(p),
                    "path": str(p),
                    "books_folder": "Books",
                })
                added += 1
        if added:
            save_devices(devices)
            self._refresh_devices()
            self._show_info(ngettext(
                "{n} device added.", "{n} devices added.", added
            ).format(n=added))
        else:
            self._show_info(_("All detected devices are already configured."))

    def _on_add_device(self, _):
        self._open_device_dialog(None, None)

    def _on_edit_device(self, _, idx: int):
        devices = load_devices()
        if idx < len(devices):
            self._open_device_dialog(devices[idx], idx)

    def _on_delete_device(self, _, idx: int):
        devices = load_devices()
        if idx < len(devices):
            devices.pop(idx)
            save_devices(devices)
            self._refresh_devices()

    def _open_device_dialog(self, device: dict | None, idx: int | None):
        d = device or {}
        dlg = Gtk.Dialog(
            title=_("Edit device") if device else _("Add device"),
            transient_for=self.get_root(),
            modal=True,
        )
        dlg.set_default_size(460, -1)
        box = dlg.get_content_area()
        box.set_spacing(6)
        box.set_margin_top(16); box.set_margin_start(16)
        box.set_margin_end(16); box.set_margin_bottom(8)

        def _dim(t):
            l = Gtk.Label(label=t); l.add_css_class("dim-label"); l.set_xalign(0)
            l.set_width_chars(10)
            return l

        # ── Name ──
        name_row = Gtk.Box(spacing=8)
        name_row.append(_dim(_("Name")))
        e_name = Gtk.Entry(); e_name.set_hexpand(True)
        e_name.set_text(d.get("name", ""))
        name_row.append(e_name)
        box.append(name_row)
        box.append(Gtk.Separator())

        # ── Cable ──
        cable_cur = d.get("path", "")
        cable_chk = Gtk.CheckButton(label=_("Cable / USB:"))
        cable_chk.set_active(bool(cable_cur))
        cable_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cable_detail.set_sensitive(bool(cable_cur))
        cable_detail.set_margin_start(20)

        cable_path_row = Gtk.Box(spacing=6)
        e_cable = Gtk.Entry(); e_cable.set_hexpand(True)
        e_cable.set_placeholder_text("/run/media/user/KOBOeReader")
        e_cable.set_text(cable_cur)
        browse_btn = Gtk.Button()
        browse_btn.set_icon_name("folder-open-symbolic")
        browse_btn.set_tooltip_text(_("Browse…"))
        browse_btn.connect("clicked", lambda _b: self._pick_folder(dlg, e_cable))
        cable_path_row.append(e_cable); cable_path_row.append(browse_btn)
        cable_detail.append(cable_path_row)

        books_row = Gtk.Box(spacing=8)
        books_row.append(_dim(_("Books folder")))
        e_books = Gtk.Entry(); e_books.set_hexpand(True)
        e_books.set_placeholder_text("Books")
        e_books.set_text(d.get("books_folder", "Books"))
        books_row.append(e_books)
        cable_detail.append(books_row)

        cable_chk.connect("toggled", lambda c: cable_detail.set_sensitive(c.get_active()))
        box.append(cable_chk); box.append(cable_detail)
        box.append(Gtk.Separator())

        # ── WiFi ──
        wifi_cur = d.get("path_wifi", "")
        wifi_ip_cur  = _wifi_ip(d) if wifi_cur else ""
        wifi_dir_cur = _wifi_folder(d).lstrip("/") if wifi_cur else ""
        wifi_chk = Gtk.CheckButton(label=_("WiFi (HTTP):"))
        wifi_chk.set_active(bool(wifi_cur))
        wifi_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wifi_detail.set_sensitive(bool(wifi_cur))
        wifi_detail.set_margin_start(20)

        wifi_conn_row = Gtk.Box(spacing=6)
        e_wifi_ip = Gtk.Entry(); e_wifi_ip.set_hexpand(True)
        e_wifi_ip.set_placeholder_text("192.168.x.x")
        e_wifi_ip.set_text(wifi_ip_cur)
        wifi_conn_row.append(e_wifi_ip)
        wifi_conn_row.append(_dim(_("Folder:")))
        e_wifi_dir = Gtk.Entry(); e_wifi_dir.set_width_chars(10)
        e_wifi_dir.set_placeholder_text("/Books")
        e_wifi_dir.set_text(wifi_dir_cur)
        wifi_conn_row.append(e_wifi_dir)
        wifi_detail.append(wifi_conn_row)

        wifi_cred_row = Gtk.Box(spacing=6)
        wifi_cred_row.append(_dim(_("User:")))
        e_wifi_user = Gtk.Entry(); e_wifi_user.set_width_chars(8)
        e_wifi_user.set_text(d.get("wifi_user", "admin"))
        wifi_cred_row.append(e_wifi_user)
        wifi_cred_row.append(_dim(_("Password:")))
        e_wifi_pass = Gtk.Entry(); e_wifi_pass.set_width_chars(8)
        e_wifi_pass.set_visibility(False)
        e_wifi_pass.set_text(d.get("wifi_pass", "admin"))
        wifi_cred_row.append(e_wifi_pass)
        wifi_detail.append(wifi_cred_row)

        wifi_note = Gtk.Label(label=_("Protocol auto-detected (KOReader / FileBrowser / BOOX Drop)."))
        wifi_note.add_css_class("caption"); wifi_note.add_css_class("dim-label")
        wifi_note.set_xalign(0); wifi_note.set_wrap(True)
        wifi_detail.append(wifi_note)

        wifi_chk.connect("toggled", lambda c: wifi_detail.set_sensitive(c.get_active()))
        box.append(wifi_chk); box.append(wifi_detail)
        box.append(Gtk.Separator())

        # ── SFTP/SSH ──
        sftp_cur = d.get("path_sftp", "")
        sftp_host_cur = ""
        sftp_dir_cur = ""
        if sftp_cur:
            rest = sftp_cur[len("sftp://"):]
            slash = rest.find("/")
            sftp_host_cur = rest[:slash] if slash >= 0 else rest
            sftp_dir_cur  = rest[slash + 1:] if slash >= 0 else ""
        sftp_chk = Gtk.CheckButton(label=_("SFTP / SSH (KOReader):"))
        sftp_chk.set_active(bool(sftp_cur))
        sftp_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sftp_detail.set_sensitive(bool(sftp_cur))
        sftp_detail.set_margin_start(20)

        sftp_conn_row = Gtk.Box(spacing=6)
        e_sftp_host = Gtk.Entry(); e_sftp_host.set_hexpand(True)
        e_sftp_host.set_placeholder_text("192.168.x.x:2222")
        e_sftp_host.set_text(sftp_host_cur)
        sftp_conn_row.append(e_sftp_host)
        sftp_conn_row.append(_dim(_("Folder:")))
        e_sftp_dir = Gtk.Entry(); e_sftp_dir.set_width_chars(10)
        e_sftp_dir.set_placeholder_text("/Books")
        e_sftp_dir.set_text(sftp_dir_cur)
        sftp_conn_row.append(e_sftp_dir)
        sftp_detail.append(sftp_conn_row)

        sftp_cred_row = Gtk.Box(spacing=6)
        sftp_cred_row.append(_dim(_("User:")))
        e_sftp_user = Gtk.Entry(); e_sftp_user.set_width_chars(8)
        e_sftp_user.set_text(d.get("ssh_user", "root"))
        sftp_cred_row.append(e_sftp_user)
        sftp_cred_row.append(_dim(_("Password:")))
        e_sftp_pass = Gtk.Entry(); e_sftp_pass.set_width_chars(8)
        e_sftp_pass.set_visibility(False)
        e_sftp_pass.set_text(d.get("ssh_pass", "root"))
        sftp_cred_row.append(e_sftp_pass)
        sftp_detail.append(sftp_cred_row)

        sftp_chk.connect("toggled", lambda c: sftp_detail.set_sensitive(c.get_active()))
        box.append(sftp_chk); box.append(sftp_detail)

        # ── status + buttons ──
        status_lbl = Gtk.Label(label="")
        status_lbl.add_css_class("dim-label"); status_lbl.set_xalign(0)
        box.append(status_lbl)

        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        ok_btn = dlg.add_button(_("Save"), Gtk.ResponseType.ACCEPT)
        ok_btn.add_css_class("suggested-action")

        def _on_response(dlg_w, resp):
            if resp == Gtk.ResponseType.ACCEPT:
                name = e_name.get_text().strip()
                if not name:
                    status_lbl.set_label(_("Name is required."))
                    return
                entry = {"name": name}
                if cable_chk.get_active():
                    cp = e_cable.get_text().strip()
                    if cp:
                        entry["path"] = cp
                        entry["books_folder"] = e_books.get_text().strip() or "Books"
                if wifi_chk.get_active():
                    wip = e_wifi_ip.get_text().strip()
                    if wip:
                        wfolder = e_wifi_dir.get_text().strip()
                        if wfolder and not wfolder.startswith("/"):
                            wfolder = "/" + wfolder
                        entry["path_wifi"] = f"wifi://{wip}{wfolder}"
                        wu = e_wifi_user.get_text().strip()
                        wp = e_wifi_pass.get_text()
                        if wu and wu != "admin":
                            entry["wifi_user"] = wu
                        if wp and wp != "admin":
                            entry["wifi_pass"] = wp
                if sftp_chk.get_active():
                    sh = e_sftp_host.get_text().strip()
                    if sh:
                        sfolder = e_sftp_dir.get_text().strip()
                        if sfolder and not sfolder.startswith("/"):
                            sfolder = "/" + sfolder
                        entry["path_sftp"] = f"sftp://{sh}{sfolder}"
                        su = e_sftp_user.get_text().strip()
                        sp = e_sftp_pass.get_text()
                        if su and su != "root":
                            entry["ssh_user"] = su
                        if sp and sp != "root":
                            entry["ssh_pass"] = sp
                if not any(k in entry for k in ("path", "path_wifi", "path_sftp")):
                    status_lbl.set_label(_("Configure at least one connection."))
                    return
                devices = load_devices()
                if idx is not None:
                    devices[idx] = entry
                else:
                    devices.append(entry)
                save_devices(devices)
                self._refresh_devices()
            dlg_w.destroy()

        dlg.connect("response", _on_response)
        dlg.show()

    def _pick_folder(self, parent, entry: Gtk.Entry):
        dialog = Gtk.FileChooserDialog(
            title=_("Select device folder"),
            transient_for=parent,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Select"), Gtk.ResponseType.ACCEPT)
        def _on_resp(d, resp):
            if resp == Gtk.ResponseType.ACCEPT:
                entry.set_text(d.get_file().get_path())
            d.destroy()
        dialog.connect("response", _on_resp)
        dialog.show()

    def _show_info(self, msg: str):
        dlg = Gtk.MessageDialog(
            transient_for=self.get_root(), modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=msg,
        )
        dlg.connect("response", lambda d, _: d.destroy())
        dlg.show()
