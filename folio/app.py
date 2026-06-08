import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio

APP_ID = "io.github.eriote.Folio"
VERSION = "0.1.0"


def main():
    app = Gtk.Application(application_id=APP_ID)
    app.connect("activate", _on_activate)
    return app.run()


def _on_activate(app):
    win = Gtk.ApplicationWindow(application=app, title="Folio")
    win.set_default_size(1060, 660)
    win.present()
