"""
First-run welcome window.
"""

import threading
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib


class WelcomeWindow(Gtk.ApplicationWindow):
    def __init__(self, app, on_import_done):
        super().__init__(application=app, title="Folio")
        self.set_default_size(520, 420)
        self.set_resizable(False)
        self._on_import_done = on_import_done
        self._folder = None
        self._build_ui()

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(48); root.set_margin_bottom(40)
        root.set_margin_start(64); root.set_margin_end(64)
        self.set_child(root)

        icon = Gtk.Image.new_from_icon_name("accessories-dictionary-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("dim-label")
        root.append(icon)

        title = Gtk.Label(label=_("Welcome to Folio"))
        title.add_css_class("title-1")
        title.set_margin_top(16)
        root.append(title)

        sub = Gtk.Label(label=_("Your personal ebook library"))
        sub.add_css_class("dim-label")
        sub.set_margin_top(4)
        sub.set_margin_bottom(32)
        root.append(sub)

        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        folder_box.set_margin_bottom(24)

        self._folder_lbl = Gtk.Label(label=_("No folder selected"))
        self._folder_lbl.add_css_class("dim-label")
        self._folder_lbl.set_hexpand(True)
        self._folder_lbl.set_xalign(0)
        self._folder_lbl.set_ellipsize(3)

        choose_btn = Gtk.Button(label=_("Choose folder…"))
        choose_btn.connect("clicked", self._on_choose_folder)

        folder_box.append(self._folder_lbl)
        folder_box.append(choose_btn)
        root.append(folder_box)

        self._import_btn = Gtk.Button(label=_("Import library"))
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.add_css_class("pill")
        self._import_btn.set_sensitive(False)
        self._import_btn.set_halign(Gtk.Align.CENTER)
        self._import_btn.connect("clicked", self._on_import)
        root.append(self._import_btn)

        self._progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._progress_box.set_margin_top(24)
        self._progress_box.set_visible(False)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(False)

        self._progress_lbl = Gtk.Label()
        self._progress_lbl.add_css_class("dim-label")
        self._progress_lbl.add_css_class("caption")
        self._progress_lbl.set_ellipsize(3)

        self._progress_box.append(self._progress_bar)
        self._progress_box.append(self._progress_lbl)
        root.append(self._progress_box)

    def _on_choose_folder(self, _btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select your ebooks folder"))
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            self._folder = Path(folder.get_path())
            self._folder_lbl.set_label(str(self._folder))
            self._folder_lbl.remove_css_class("dim-label")
            self._import_btn.set_sensitive(True)
        except Exception:
            pass

    def _on_import(self, _btn):
        if not self._folder:
            return
        self._import_btn.set_sensitive(False)
        self._progress_box.set_visible(True)

        def _bg():
            from folio.scanner import scan_folder
            for current, total, title in scan_folder(self._folder):
                frac = current / total if total else 1.0
                GLib.idle_add(self._update_progress, frac, f"{current}/{total} — {title}")
            GLib.idle_add(self._import_finished)

        threading.Thread(target=_bg, daemon=True).start()

    def _update_progress(self, frac, label):
        self._progress_bar.set_fraction(frac)
        self._progress_lbl.set_label(label)

    def _import_finished(self):
        self._progress_lbl.set_label(_("Done!"))
        GLib.timeout_add(600, self._open_main)

    def _open_main(self):
        self._on_import_done()
        self.close()
        return False
