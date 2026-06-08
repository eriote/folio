from folio.i18n import setup as _setup_i18n
_setup_i18n()

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from folio.database import count_books

APP_ID  = "io.github.eriote.Folio"
VERSION = "0.1.0"


def main():
    app = Gtk.Application(application_id=APP_ID)
    app.connect("activate", _on_activate)
    return app.run()


def _on_activate(app):
    if count_books() == 0:
        _show_welcome(app)
    else:
        _show_main(app)


def _show_welcome(app):
    from folio.ui.welcome import WelcomeWindow
    win = WelcomeWindow(app, on_import_done=lambda: _show_main(app))
    win.present()


def _show_main(app):
    from folio.ui.window import MainWindow
    win = MainWindow(app)
    win.present()
