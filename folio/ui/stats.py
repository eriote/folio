"""
Reading statistics page.
"""

import csv
import io
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from folio.database import get_reading_stats
from folio.paths import PREFS_FILE

import json

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_CHART_H = 120  # max bar height in pixels


def _load_prefs() -> dict:
    try:
        return json.loads(PREFS_FILE.read_text())
    except Exception:
        return {}


def _save_prefs(p: dict) -> None:
    try:
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(p))
    except Exception:
        pass


def _make_kpi_card(value: str, label: str) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    card.add_css_class("card")
    card.set_hexpand(True)

    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    inner.set_margin_start(16); inner.set_margin_end(16)
    inner.set_margin_top(12); inner.set_margin_bottom(12)

    val = Gtk.Label(label=value)
    val.add_css_class("title-1")
    val.set_xalign(0)
    inner.append(val)

    lbl = Gtk.Label(label=label)
    lbl.add_css_class("dim-label")
    lbl.add_css_class("caption")
    lbl.set_xalign(0)
    inner.append(lbl)

    card.append(inner)
    return card, val, lbl


class StatsPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._profile_id: int | None = None
        self._year: int | None = None
        self._stats: dict = {}
        self._build_ui()

    def _build_ui(self):
        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(16); toolbar.set_margin_end(16)
        toolbar.set_margin_top(10); toolbar.set_margin_bottom(10)

        year_lbl = Gtk.Label(label=_("Year:"))
        year_lbl.add_css_class("dim-label")
        toolbar.append(year_lbl)

        self._year_dd = Gtk.DropDown()
        self._year_model = Gtk.StringList.new([_("All time")])
        self._year_dd.set_model(self._year_model)
        self._year_dd.connect("notify::selected", self._on_year_changed)
        toolbar.append(self._year_dd)

        spacer = Gtk.Box(); spacer.set_hexpand(True)
        toolbar.append(spacer)

        goal_lbl = Gtk.Label(label=_("Annual goal:"))
        goal_lbl.add_css_class("dim-label")
        toolbar.append(goal_lbl)

        self._goal_spin = Gtk.SpinButton()
        self._goal_spin.set_range(0, 9999)
        self._goal_spin.set_increments(1, 10)
        self._goal_spin.set_value(_load_prefs().get("reading_goal", 52))
        self._goal_spin.connect("value-changed", self._on_goal_changed)
        toolbar.append(self._goal_spin)

        export_btn = Gtk.MenuButton()
        export_btn.set_icon_name("document-save-symbolic")
        export_btn.set_tooltip_text(_("Export"))
        export_btn.add_css_class("flat")

        export_pop = Gtk.Popover()
        exp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        exp_box.set_margin_top(4); exp_box.set_margin_bottom(4)
        exp_box.set_margin_start(4); exp_box.set_margin_end(4)

        csv_btn = Gtk.Button(label=_("Export CSV"))
        csv_btn.add_css_class("flat")
        csv_btn.connect("clicked", lambda _b: (export_pop.popdown(), self._export_csv()))
        exp_box.append(csv_btn)

        md_btn = Gtk.Button(label=_("Export Markdown"))
        md_btn.add_css_class("flat")
        md_btn.connect("clicked", lambda _b: (export_pop.popdown(), self._export_markdown()))
        exp_box.append(md_btn)

        export_pop.set_child(exp_box)
        export_btn.set_popover(export_pop)
        toolbar.append(export_btn)

        self.append(toolbar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Scrollable content ────────────────────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(content)
        self.append(scroll)

        # KPI cards row
        self._kpi_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._kpi_row.set_margin_start(16); self._kpi_row.set_margin_end(16)
        self._kpi_row.set_margin_top(16); self._kpi_row.set_margin_bottom(8)

        self._card_books, self._kpi_books_val, _ = _make_kpi_card("0", _("books read"))
        self._kpi_row.append(self._card_books)
        self._card_pages, self._kpi_pages_val, _ = _make_kpi_card("0", _("pages read"))
        self._kpi_row.append(self._card_pages)
        self._card_authors, self._kpi_authors_val, _ = _make_kpi_card("0", _("authors"))
        self._kpi_row.append(self._card_authors)

        content.append(self._kpi_row)

        # Annual goal bar
        self._goal_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._goal_box.set_margin_start(16); self._goal_box.set_margin_end(16)
        self._goal_box.set_margin_bottom(16)

        goal_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._goal_heading = Gtk.Label()
        self._goal_heading.add_css_class("heading")
        self._goal_heading.set_xalign(0)
        self._goal_heading.set_hexpand(True)
        goal_row.append(self._goal_heading)
        self._goal_pct_lbl = Gtk.Label()
        self._goal_pct_lbl.add_css_class("dim-label")
        self._goal_pct_lbl.add_css_class("caption")
        goal_row.append(self._goal_pct_lbl)
        self._goal_box.append(goal_row)

        self._goal_bar = Gtk.ProgressBar()
        self._goal_box.append(self._goal_bar)

        content.append(self._goal_box)

        # Monthly chart heading
        chart_heading = Gtk.Label(label=_("Books read per month"))
        chart_heading.add_css_class("title-4")
        chart_heading.set_xalign(0)
        chart_heading.set_margin_start(16)
        chart_heading.set_margin_top(8)
        chart_heading.set_margin_bottom(8)
        content.append(chart_heading)

        # Bar chart
        self._chart_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._chart_box.set_margin_start(16); self._chart_box.set_margin_end(16)
        self._chart_box.set_margin_bottom(16)
        self._chart_box.set_homogeneous(True)
        content.append(self._chart_box)

        # Top authors heading
        authors_heading = Gtk.Label(label=_("Top authors"))
        authors_heading.add_css_class("title-4")
        authors_heading.set_xalign(0)
        authors_heading.set_margin_start(16)
        authors_heading.set_margin_top(8)
        authors_heading.set_margin_bottom(8)
        content.append(authors_heading)

        self._authors_list = Gtk.ListBox()
        self._authors_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._authors_list.add_css_class("boxed-list")
        self._authors_list.set_margin_start(16)
        self._authors_list.set_margin_end(16)
        self._authors_list.set_margin_bottom(24)
        content.append(self._authors_list)

    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self, profile_id: int | None = None):
        if profile_id is not None:
            self._profile_id = profile_id
        if self._profile_id is None:
            from folio.database import get_or_create_default_profile
            self._profile_id = get_or_create_default_profile()
        threading.Thread(target=self._bg_load, daemon=True).start()

    # ── Data loading ──────────────────────────────────────────────────────────

    def _bg_load(self):
        stats = get_reading_stats(self._profile_id, self._year)
        GLib.idle_add(self._populate, stats)

    def _populate(self, stats: dict):
        self._stats = stats

        # Update year dropdown (add any newly-discovered years)
        current_items = [self._year_model.get_string(i)
                         for i in range(self._year_model.get_n_items())]
        for y in stats.get("years", []):
            if str(y) not in current_items:
                self._year_model.append(str(y))

        # KPIs
        self._kpi_books_val.set_label(str(stats["total_books"]))
        pages = stats["total_pages"]
        self._kpi_pages_val.set_label(f"{pages:,}".replace(",", " "))
        self._kpi_authors_val.set_label(str(stats["unique_authors"]))

        # Annual goal
        goal = int(self._goal_spin.get_value())
        total = stats["total_books"]
        if goal > 0:
            frac = min(1.0, total / goal)
            pct = int(frac * 100)
            self._goal_heading.set_label(
                _("Goal: {done} of {goal} books").format(done=total, goal=goal)
            )
            self._goal_pct_lbl.set_label(f"{pct}%")
            self._goal_bar.set_fraction(frac)
            self._goal_box.set_visible(True)
        else:
            self._goal_box.set_visible(False)

        # Bar chart
        monthly = stats["monthly"]
        max_count = max(monthly.values(), default=1) or 1
        while self._chart_box.get_first_child():
            self._chart_box.remove(self._chart_box.get_first_child())

        for i, month_name in enumerate(_MONTHS):
            month_key = f"{i+1:02d}"
            count = monthly.get(month_key, 0)
            bar_h = max(2, int(_CHART_H * count / max_count)) if count else 0

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_valign(Gtk.Align.END)
            col.set_hexpand(True)

            count_lbl = Gtk.Label(label=str(count) if count else "")
            count_lbl.add_css_class("caption")
            count_lbl.add_css_class("dim-label")
            col.append(count_lbl)

            bar_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bar_wrap.set_valign(Gtk.Align.END)
            bar_wrap.set_vexpand(True)
            bar_wrap.set_size_request(-1, _CHART_H)

            if bar_h > 0:
                bar = Gtk.Box()
                bar.add_css_class("accent")
                bar.set_size_request(-1, bar_h)
                bar.set_valign(Gtk.Align.END)
                bar_wrap.append(bar)
            col.append(bar_wrap)

            mo_lbl = Gtk.Label(label=_(month_name))
            mo_lbl.add_css_class("caption")
            mo_lbl.add_css_class("dim-label")
            col.append(mo_lbl)

            self._chart_box.append(col)

        # Top authors
        while self._authors_list.get_first_child():
            self._authors_list.remove(self._authors_list.get_first_child())

        for rank, (author, count) in enumerate(stats["top_authors"], 1):
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(12); box.set_margin_end(12)
            box.set_margin_top(8); box.set_margin_bottom(8)

            rank_lbl = Gtk.Label(label=str(rank))
            rank_lbl.add_css_class("dim-label")
            rank_lbl.add_css_class("caption")
            rank_lbl.set_size_request(20, -1)
            box.append(rank_lbl)

            name_lbl = Gtk.Label(label=author)
            name_lbl.set_xalign(0)
            name_lbl.set_hexpand(True)
            name_lbl.set_ellipsize(3)
            box.append(name_lbl)

            cnt_lbl = Gtk.Label(
                label=ngettext("{n} book", "{n} books", count).format(n=count)
            )
            cnt_lbl.add_css_class("dim-label")
            cnt_lbl.add_css_class("caption")
            box.append(cnt_lbl)

            row.set_child(box)
            self._authors_list.append(row)

        if not stats["top_authors"]:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            lbl = Gtk.Label(label=_("No data yet."))
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(16); lbl.set_margin_bottom(16)
            row.set_child(lbl)
            self._authors_list.append(row)

    # ── Filters / controls ────────────────────────────────────────────────────

    def _on_year_changed(self, dd, _param):
        idx = dd.get_selected()
        if idx == 0:
            self._year = None
        else:
            try:
                self._year = int(self._year_model.get_string(idx))
            except ValueError:
                self._year = None
        self.refresh()

    def _on_goal_changed(self, spin):
        prefs = _load_prefs()
        prefs["reading_goal"] = int(spin.get_value())
        _save_prefs(prefs)
        if self._stats:
            self._populate(self._stats)

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_csv(self):
        stats = self._stats
        if not stats:
            return
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Month", "Books read"])
        for i, name in enumerate(_MONTHS):
            w.writerow([name, stats["monthly"].get(f"{i+1:02d}", 0)])
        w.writerow([])
        w.writerow(["Author", "Books"])
        for author, count in stats["top_authors"]:
            w.writerow([author, count])
        self._save_file("reading_stats.csv", buf.getvalue().encode())

    def _export_markdown(self):
        stats = self._stats
        if not stats:
            return
        year_label = str(self._year) if self._year else _("All time")
        lines = [
            f"# Reading statistics — {year_label}",
            "",
            f"- **Books read:** {stats['total_books']}",
            f"- **Pages read:** {stats['total_pages']:,}".replace(",", " "),
            f"- **Authors:** {stats['unique_authors']}",
            "",
            "## Per month",
            "",
            "| Month | Books |",
            "|-------|-------|",
        ]
        for i, name in enumerate(_MONTHS):
            c = stats["monthly"].get(f"{i+1:02d}", 0)
            lines.append(f"| {name} | {c} |")
        lines += ["", "## Top authors", ""]
        for rank, (author, count) in enumerate(stats["top_authors"], 1):
            lines.append(f"{rank}. {author} ({count})")
        self._save_file("reading_stats.md", "\n".join(lines).encode())

    def _save_file(self, filename: str, data: bytes):
        dialog = Gtk.FileChooserDialog(
            title=_("Save file"),
            transient_for=self.get_root(),
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.set_current_name(filename)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.ACCEPT)
        dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT).add_css_class("suggested-action")

        def on_resp(d, resp):
            if resp == Gtk.ResponseType.ACCEPT:
                path = d.get_file().get_path()
                if path:
                    try:
                        with open(path, "wb") as f:
                            f.write(data)
                    except Exception as exc:
                        err = Gtk.MessageDialog(
                            transient_for=self.get_root(), modal=True,
                            message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.OK, text=str(exc),
                        )
                        err.connect("response", lambda e, _: e.destroy())
                        err.show()
            d.destroy()

        dialog.connect("response", on_resp)
        dialog.show()
