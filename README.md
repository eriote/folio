# Folio

[![License: GPL v3+](https://img.shields.io/badge/license-GPLv3+-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![GTK 4](https://img.shields.io/badge/GTK-4-blueviolet.svg)](https://www.gtk.org)
[![Build](https://github.com/eriote/folio/actions/workflows/build.yml/badge.svg)](https://github.com/eriote/folio/actions/workflows/build.yml)

**A GTK4 desktop app to manage your personal ebook collection.**

Point Folio at a folder of EPUBs and it indexes everything in a local SQLite
database. Browse, search, track what you've read, manage your e-reader devices,
and watch your reading habits grow over time.

![Folio main view](screenshots/library.png)

---

## Features

- 📚 **Import & browse** — drop a folder of EPUBs, get a cover grid you can search and sort
- 📖 **Reading log** — *want to read* / *reading* / *read*, with start and finish dates
- 🎯 **Annual goal** — track your year with a progress bar and a monthly chart
- 👤 **Profiles** — multiple users, independent logs, switch from the header
- 📑 **External books** — paper books or those read in other apps still count
- ✏️ **Edit metadata** — title, author, series, year, cover; embed the cover back into the EPUB
- 📤 **Send to device** — cable, WiFi (Filebrowser) or SFTP; progress shown live
- 📲 **KoReader stats** — reading streak, speed, best hour, weekly email summary
- 🔮 **Discover** — series continuations, more from favourite authors, oldest TBR
- 🌍 **Localised** — English and Spanish (other languages welcome via PRs)
- 💾 **Export** — your reading log to CSV or Markdown for archival or sharing

## Screenshots

| Library grid | Reading log | Stats |
|:---:|:---:|:---:|
| ![library](screenshots/library.png) | ![reading](screenshots/reading.png) | ![stats](screenshots/stats.png) |

## Installation

### From source (recommended for now)

```bash
git clone https://github.com/eriote/folio
cd folio
pipx install .
```

Or with `pip` inside a venv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install .
```

Then launch:

```bash
folio
```

### System dependencies

Folio needs GTK 4 and PyGObject. On Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 libgtk-4-dev
```

On Fedora:

```bash
sudo dnf install python3-gobject gtk4-devel
```

On Arch:

```bash
sudo pacman -S python-gobject gtk4
```

### Optional: SFTP transfer to Kindle / e-reader

If you want to send books over SFTP (Kindle with USBNetwork, KOReader Plugin, etc.):

```bash
pipx install ".[sftp]"
```

## Usage

On first run Folio asks for a folder containing your EPUBs and scans it. The
scan builds a SQLite database at `~/.local/share/folio/folio.db` plus a covers
directory next to it. Your epubs are **not moved** — Folio just indexes them in
place.

From there:

- The **Library** tab shows the cover grid. Click a book to see details.
- **Reading** is your log: add books to *want to read*, mark them *reading*, then *read*.
- **Edit** lets you tweak metadata and replace the cover.
- **Discover** picks suggestions from your collection.
- **Settings** holds profiles, devices and language.

## Configuration

Data and config locations follow the XDG spec:

| Path | Contents |
|---|---|
| `~/.local/share/folio/folio.db` | SQLite database (books, profiles, reading log) |
| `~/.local/share/folio/covers/` | Cached cover thumbnails |
| `~/.config/folio/devices.json` | Configured e-reader devices |

Override the data directory with `FOLIO_DATA_DIR=/some/other/path folio`.

## Development

```bash
git clone https://github.com/eriote/folio
cd folio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
folio
```

Source layout:

```
folio/
├── app.py          # Application bootstrap
├── database.py     # SQLite layer (single source of truth)
├── scanner.py      # EPUB import / metadata extraction
├── devices.py      # Cable / WiFi / SFTP transfer
├── i18n.py         # gettext setup
├── paths.py        # XDG paths
├── locale/         # Translations (.po / .mo)
└── ui/
    ├── window.py        # Main window + library grid
    ├── book_detail.py   # Per-book detail
    ├── reading.py       # Reading log views
    ├── stats.py         # Annual stats and charts
    ├── discover.py      # Suggestions
    ├── edit_books.py    # Metadata editor
    ├── device_page.py   # Per-device library / KoReader stats
    ├── settings.py      # Settings page
    └── welcome.py       # First-run wizard
```

### Translations

Folio uses gettext. To start a new language:

```bash
xgettext -L Python -o folio/locale/folio.pot folio/**/*.py
msginit -i folio/locale/folio.pot -o folio/locale/<lang>/LC_MESSAGES/folio.po -l <lang>
# … translate folio.po …
msgfmt folio/locale/<lang>/LC_MESSAGES/folio.po -o folio/locale/<lang>/LC_MESSAGES/folio.mo
```

PRs welcome.

## Roadmap

- [ ] Flatpak / Flathub submission
- [ ] AppImage builds in CI
- [ ] Per-book personal tags
- [ ] KoReader highlights extraction and Markdown export
- [ ] OPDS server mode

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
