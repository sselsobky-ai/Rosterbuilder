"""Main window: sidebar, month bar, pages, menus, status bar."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QFont, QKeySequence, QPainter
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QStackedWidget, QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import count_phrase, info_box, month_name, tr, tr_msg
from ..importer import ImportError as ImportFailed
from ..importer import import_excel
from ..store import Store, StoreError
from .build_page import BuildPage
from .checks_panel import ChecksPage
from .context import AppContext
from .coverage_page import CoveragePage
from .employees_page import EmployeesPage
from .help_dialogs import AboutDialog, UserGuideDialog
from .month_page import DemandPage, FixedShiftsPage, LeavePage, MonthPage, NewMonthDialog, month_title
from .receptions_page import ReceptionsPage
from .roster_page import RosterPage
from .settings_page import SettingsPage
from .shifts_page import ShiftsPage
from .welcome import ImportReportDialog, SetupGuide, WelcomePage

BANNER_TEXT = "This month is built – inputs are locked. Re-open to edit."


class PlaceholderPage(QWidget):
    """A screen of a later step."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("page")
        self.key = title.lower()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        t = QLabel(title)
        t.setProperty("role", "title")
        lay.addWidget(t)
        c = QLabel(tr("Coming in the next step."))
        c.setProperty("role", "subtitle")
        lay.addWidget(c)
        lay.addStretch(1)

    def flush(self) -> None: ...


BADGE_ROLE = Qt.ItemDataRole.UserRole + 1


class SidebarDelegate(QStyledItemDelegate):
    """Normal item painting plus a red count badge on the right for items that carry a count."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        n = index.data(BADGE_ROLE)
        if not n:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(option.font)
        f.setPointSizeF(8.5)
        f.setBold(True)
        painter.setFont(f)
        text = str(n) if n < 100 else "99+"
        w = max(22, painter.fontMetrics().horizontalAdvance(text) + 14)
        if option.direction == Qt.LayoutDirection.RightToLeft:  # the badge sits on the left edge of a mirrored list
            r = QRect(option.rect.left() + 12, option.rect.center().y() - 9, w, 18)
        else:
            r = QRect(option.rect.right() - w - 12, option.rect.center().y() - 9, w, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#DC2626"))
        painter.drawRoundedRect(r, 9, 9)
        painter.setPen(QColor("white"))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class MainWindow(QMainWindow):
    """The roster app window.  ``settings`` is the QSettings used to remember size and position."""

    def __init__(self, store: Store, settings: QSettings | None = None, ctx: AppContext | None = None) -> None:
        super().__init__()
        self.setWindowTitle(tr("Roster Builder"))
        self.qsettings = settings or QSettings("RosterBuilder", "RosterBuilder")
        self.successor: MainWindow | None = None  # set when the language was switched (the new window replaces this one)
        if ctx is None:
            self.ctx = AppContext(store, self)
        else:  # language switch: the same store, month and undo history move to the new window
            self.ctx = ctx
            ctx.setParent(self)
        self.ctx.saved.connect(self._on_saved)
        self.ctx.message.connect(self._on_message)
        self.ctx.month_changed.connect(self._update_month_bar)
        self.ctx.request_new_month.connect(self.new_month)
        self.ctx.request_delete_month.connect(self.delete_month)
        self.ctx.request_reopen_month.connect(self.reopen_month)
        self.ctx.request_jump.connect(self.jump)
        self.last_report_dialog: ImportReportDialog | None = None
        self.last_help_dialog = None

        # the window shows either the app or the welcome page (empty database)
        self.root = QStackedWidget()
        self.setCentralWidget(self.root)
        central = QWidget()
        self.root.addWidget(central)
        self.welcome = WelcomePage()
        self.welcome.import_clicked.connect(self.import_from_excel)
        self.welcome.empty_clicked.connect(self.start_empty)
        self.welcome.language_clicked.connect(self.switch_language)
        self.root.addWidget(self.welcome)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_month_bar())

        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(200)
        self.sidebar.setItemDelegate(SidebarDelegate(self.sidebar))
        body.addWidget(self.sidebar)

        # content column: banner and setup strip sit ABOVE the page title, inside the content area (the sidebar never moves)
        content = QVBoxLayout()
        content.setSpacing(0)
        body.addLayout(content, 1)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(16, 8, 16, 8)
        self.banner_label = QLabel(tr(BANNER_TEXT))
        bl.addWidget(self.banner_label, 1)
        self.reopen_btn = QPushButton(tr("Re-open month"))
        self.reopen_btn.setToolTip(tr("Unlock this month so its inputs can be edited again."))
        self.reopen_btn.clicked.connect(self.reopen_month)
        bl.addWidget(self.reopen_btn)
        self.banner_host = QWidget()
        bwl = QVBoxLayout(self.banner_host)
        bwl.setContentsMargins(24, 12, 24, 0)
        bwl.addWidget(self.banner)
        content.addWidget(self.banner_host)
        self.banner_host.hide()
        self.guide = SetupGuide()
        self.guide.go.connect(self.go)
        self.guide.dismissed.connect(self.hide_guide)
        self.guide_host = QWidget()
        gl = QVBoxLayout(self.guide_host)
        gl.setContentsMargins(24, 12, 24, 0)
        gl.addWidget(self.guide)
        content.addWidget(self.guide_host)
        self.guide_host.hide()
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)

        self.pages: dict[str, QWidget] = {}
        self._nav_index: dict[str, int] = {}
        self._add_header(tr("SET UP ONCE"))
        for page in (SettingsPage(self.ctx), ReceptionsPage(self.ctx), ShiftsPage(self.ctx), CoveragePage(self.ctx), EmployeesPage(self.ctx)):
            self._add_page(tr(page.title), page)
        self._add_header(tr("THIS MONTH"))
        for label, page in (("Month", MonthPage(self.ctx)), ("Leave", LeavePage(self.ctx)), ("Fixed shifts", FixedShiftsPage(self.ctx)),
                            ("Demand", DemandPage(self.ctx)), ("Build", BuildPage(self.ctx)), ("Roster", RosterPage(self.ctx)),
                            ("Checks", ChecksPage(self.ctx))):
            self._add_page(tr(label), page)
        self.checks: ChecksPage = self.pages["checks"]  # type: ignore[assignment]
        self.checks.count_changed.connect(self._set_badge)
        self.build: BuildPage = self.pages["build"]  # type: ignore[assignment]
        self.roster: RosterPage = self.pages["roster"]  # type: ignore[assignment]
        self.build.built.connect(lambda _res: self.go("roster"))  # after a successful build show the roster
        self.roster.problems_changed.connect(self._set_roster_badge)
        self.ctx.request_open_page.connect(self.open_page)
        self.ctx.request_rebuild.connect(self.rebuild_from_roster)
        self.sidebar.currentRowChanged.connect(self._nav)
        self.ctx.changed.connect(lambda _k, _o: self._update_guide())

        self._build_menus()
        self.saved_label = QLabel("")
        self.saved_label.setProperty("role", "ok")
        self.statusBar().addPermanentWidget(self.saved_label)
        self.statusBar().showMessage(tr("Ready"))

        self._restore_geometry()
        self._update_month_bar()
        self.go("settings")
        for p in self.real_pages():
            p.refresh()
        if self.ctx.month_info:
            self.checks.run_now()
        self._update_welcome()
        self.sidebar.setFocus()

    # ------------------------------------------------------------------ structure
    def _add_header(self, text: str) -> None:
        it = QListWidgetItem(text)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.sidebar.addItem(it)

    def _add_page(self, label: str, page: QWidget) -> None:
        key = getattr(page, "key", "") or label.lower()
        it = QListWidgetItem(label)
        self.sidebar.addItem(it)
        idx = self.stack.addWidget(page)
        self.pages[key] = page
        self._nav_index[key] = self.sidebar.count() - 1
        it.setData(Qt.ItemDataRole.UserRole, idx)

    def real_pages(self):
        return [p for p in self.pages.values() if hasattr(p, "refresh")]

    def _nav(self, row: int) -> None:
        it = self.sidebar.item(row)
        if it is not None and it.data(Qt.ItemDataRole.UserRole) is not None:
            self.stack.setCurrentIndex(it.data(Qt.ItemDataRole.UserRole))
        self.banner_host.setVisible(self.ctx.read_only and self.current_key() != "roster")  # the grid needs the height
        self._update_guide()

    def go(self, key: str) -> None:
        """Show a page by its key (``"settings"``, ``"employees"``, ``"fixed_shifts"`` …)."""
        k = key.lower().replace(" ", "_")
        self.sidebar.setCurrentRow(self._nav_index[k])

    def current_key(self) -> str:
        return getattr(self.stack.currentWidget(), "key", "")

    def jump(self, loc, message: str = "") -> None:
        """Open the page of a locator (screen, record id, field) and mark the record and field."""
        self.flush_pages()
        self.go(loc.screen)
        page = self.pages[loc.screen.lower()]
        if (loc.record_id is not None or loc.field) and hasattr(page, "focus_record"):
            page.focus_record(loc.record_id, loc.field, message)
            if message and hasattr(page, "show_message"):
                page.show_message(message)  # the tooltip says the same, but this one is visible without hovering

    def open_page(self, key: str) -> None:
        self.flush_pages()
        self.go(key)
        if key == "checks":
            self.checks.run_now()

    def rebuild_from_roster(self, keep: bool = True) -> None:
        """Roster page -> "Rebuild keeping my changes": switch to Build and start (the Build page asks to confirm)."""
        self.flush_pages()
        self.go("build")
        self.build.start_build(rebuild=True, keep_edits=keep)

    def _set_roster_badge(self, n: int) -> None:
        it = self.sidebar.item(self._nav_index["roster"])
        it.setData(BADGE_ROLE, n)
        self.sidebar.viewport().update()

    def _set_badge(self, n: int) -> None:
        it = self.sidebar.item(self._nav_index["checks"])
        it.setData(BADGE_ROLE, n)
        self.sidebar.viewport().update()
        self.check_btn.setText(tr('Check inputs  ({n})', n=n) if n else tr("Check inputs"))
        self.check_btn.setProperty("alert", bool(n))
        self.check_btn.style().unpolish(self.check_btn)
        self.check_btn.style().polish(self.check_btn)
        self.check_btn.setToolTip(tr("{c} in this month's inputs. Click to see them.", c=count_phrase(n, "finding")) if n
                                  else tr("Check this month's inputs. No problems found at the last check."))

    # ------------------------------------------------------------------ month bar
    def _build_month_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("monthbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(8)
        title = QLabel(tr("Roster Builder"))
        title.setStyleSheet("font-weight: 700; font-size: 12pt; color: #2563EB;")
        h.addWidget(title)
        h.addSpacing(24)
        rtl = i18n.is_rtl()
        self.prev_btn = QPushButton("▶" if rtl else "◀")
        self.prev_btn.setProperty("navbtn", True)
        self.prev_btn.setToolTip(tr("Previous month"))
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn = QPushButton("◀" if rtl else "▶")
        self.next_btn.setProperty("navbtn", True)
        self.next_btn.setToolTip(tr("Next month"))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.month_label = QLabel("")
        self.month_label.setObjectName("monthLabel")
        self.month_label.setMinimumWidth(170)
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_chip = QLabel("")
        self.status_chip.setObjectName("statusChip")
        self.new_btn = QPushButton(tr("New month"))
        self.new_btn.setToolTip(tr("Start a new month from your current staff, shifts and coverage."))
        self.new_btn.clicked.connect(self.new_month)
        self.check_btn = QPushButton(tr("Check inputs"))
        self.check_btn.setToolTip(tr("Check this month's inputs."))
        self.check_btn.setMinimumWidth(150)
        self.check_btn.clicked.connect(self.show_checks)
        for b in (self.prev_btn, self.next_btn, self.new_btn, self.check_btn):
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        for w in (self.prev_btn, self.month_label, self.next_btn, self.status_chip):
            h.addWidget(w)
        h.addSpacing(8)
        h.addWidget(self.new_btn)
        h.addStretch(1)
        h.addWidget(self.check_btn)
        return bar

    def show_checks(self) -> None:
        self.flush_pages()
        self.go("checks")
        self.checks.run_now()

    def _update_month_bar(self) -> None:
        ctx = self.ctx
        mi = ctx.month_info
        ms = [m.key for m in ctx.store.list_months()]
        if mi:
            self.month_label.setText(month_title(mi.key))
            built = mi.status != "draft"
            self.status_chip.setText(tr("Built") if built else tr("Draft"))
            self.status_chip.setProperty("built", built)
            self.status_chip.style().unpolish(self.status_chip)
            self.status_chip.style().polish(self.status_chip)
            self.status_chip.show()
            i = ms.index(mi.key)
            self.prev_btn.setEnabled(i > 0)
            self.next_btn.setEnabled(i < len(ms) - 1)
        else:
            self.month_label.setText(tr("No month yet"))
            self.status_chip.hide()
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
        self.banner_host.setVisible(ctx.read_only and self.current_key() != "roster")
        self._update_welcome()
        self._update_guide()

    def _step(self, d: int) -> None:
        ms = [m.key for m in self.ctx.store.list_months()]
        if self.ctx.month in ms:
            j = ms.index(self.ctx.month) + d
            if 0 <= j < len(ms):
                self.flush_pages()
                self.ctx.set_month(ms[j])

    def new_month(self) -> None:
        ms = self.ctx.store.list_months()
        if ms:
            y, m = ms[-1].key
            m += 1
            if m > 12:
                y, m = y + 1, 1
        else:
            t = date.today()
            y, m = t.year, t.month
        dlg = NewMonthDialog(self, y, m, bool(ms), [x.key for x in ms])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.create_month(*dlg.value(), copy_fixed=dlg.copy_fixed_value())

    def create_month(self, year: int, month: int, copy_fixed: bool = False) -> bool:
        self.flush_pages()
        try:
            self.ctx.store.create_month(year, month, copy_from="previous" if self.ctx.store.list_months() else None,
                                        copy_fixed=copy_fixed)
        except StoreError as exc:
            self._on_message(tr_msg(str(exc)), "error")
            return False
        self.ctx.set_month((year, month))
        self._on_saved(tr('Month {v0} {year} created', v0=month_name(month), year=year))
        return True

    def delete_month(self) -> bool:
        mi = self.ctx.month_info
        if mi is None:
            return False
        if mi.status != "draft":
            self._on_message(tr("A built month is protected. Re-open it first, then delete it."), "error")
            return False
        if not self.ctx.confirm(tr('Delete {v0}? Its leave, fixed shifts and demand rows are removed. Your staff, shifts and settings are not touched.', v0=month_title(mi.key))):
            return False
        self.flush_pages()
        self.ctx.store.delete_month(mi.key)
        left = [m.key for m in self.ctx.store.list_months()]
        before = [k for k in left if k < mi.key]
        self.ctx.set_month((before or left or [None])[-1] if (before or left) else None)
        self._on_saved(tr('{v0} deleted', v0=month_title(mi.key)))
        return True

    def reopen_month(self) -> None:
        if self.ctx.confirm(tr("Re-open this month? Its inputs become editable again and the built roster is no longer protected.")):
            self.ctx.reopen_month()

    # ------------------------------------------------------------------ welcome / import / first run
    def _is_empty(self) -> bool:
        st = self.ctx.store
        return not st.has_master_data() and not st.list_months()

    def _update_welcome(self) -> None:
        self.root.setCurrentWidget(self.welcome if self._is_empty() else self.root.widget(0))

    def show_error(self, title: str, text: str) -> None:
        """A friendly message box (tests replace this)."""
        QMessageBox.warning(self, title, text)

    def pick_workbook(self) -> str:
        """Ask for the Excel file to import (tests replace this)."""
        path, _f = QFileDialog.getOpenFileName(self, tr("Import from Excel"), "", tr("Excel workbooks (*.xlsx *.xlsm)"))
        return path

    def import_from_excel(self) -> bool:
        """Pick a workbook and import it.  On a database that already holds data the user must confirm the replacement."""
        path = self.pick_workbook()
        if not path:
            return False
        st = self.ctx.store
        replace = st.has_master_data()
        if replace and not self.ctx.confirm(
                tr("Replace your current staff, shifts, coverage and draft months with the contents of this workbook?\n\n"
                "Built months are kept. A backup of your data is made first.")):
            return False
        self.flush_pages()
        try:
            if replace:
                try:
                    st.backup()
                except StoreError:
                    pass
            rep = import_excel(st, path, mode="replace" if (replace or st.list_months()) else "empty", confirmed=True)
        except ImportFailed as exc:
            self.show_error(tr("Import"), tr_msg(str(exc)))
            self._on_message(tr_msg(str(exc)), "error")
            return False
        self.ctx.set_store(st)
        self._update_welcome()
        self.go("month")
        self._on_saved(tr("Imported {name}", name=Path(path).name))
        dlg = ImportReportDialog(self, rep)
        self.last_report_dialog = dlg
        dlg.exec()
        self.checks.run_now()
        return True

    def start_empty(self) -> None:
        """Begin with an empty setup: create this month as a draft and open Settings with the setup checklist."""
        t = date.today()
        if not self.ctx.store.has_month((t.year, t.month)):
            self.ctx.store.create_month(t.year, t.month)
        self.ctx.set_store(self.ctx.store)
        self.qsettings.setValue("guide/active", True)
        self._update_welcome()
        self.go("settings")
        self._update_guide()
        self._on_saved(tr("Started with an empty setup"))

    def hide_guide(self) -> None:
        self.qsettings.setValue("guide/active", False)
        self._update_guide()

    def guide_active(self) -> bool:
        v = self.qsettings.value("guide/active", False)
        return str(v).lower() in ("true", "1")

    def _update_guide(self) -> None:
        on = self.guide_active() and not self._is_empty()
        self.guide_host.setVisible(on)
        if not on:
            return
        view = self.ctx.view()
        done = {"receptions": bool(view.list_receptions()), "shifts": bool(view.list_shifts()),
                "coverage": bool(view.list_targets()), "employees": bool(view.list_employees()),
                "month": bool(self.ctx.month_info)}
        self.guide.update_state(done, self.current_key())

    # ------------------------------------------------------------------ menus
    def _build_menus(self) -> None:
        mb = self.menuBar()
        fm = mb.addMenu(tr("&File"))
        self.act_import = QAction(tr("&Import from Excel…"), self)
        self.act_import.setToolTip(tr("Fill the screens from an old roster workbook (.xlsx / .xlsm)."))
        self.act_import.triggered.connect(self.import_from_excel)
        self.act_export_xlsx = QAction(tr("Export roster to &Excel…"), self)
        self.act_export_xlsx.triggered.connect(lambda: self.roster.export_excel())
        self.act_export_pdf = QAction(tr("Export roster to &PDF…"), self)
        self.act_export_pdf.triggered.connect(lambda: self.roster.export_pdf())
        self.act_print = QAction(tr("&Print roster…"), self)
        self.act_print.setShortcut(QKeySequence("Ctrl+P"))
        self.act_print.triggered.connect(lambda: self.roster.print_roster())
        self.act_backup = QAction(tr("&Backup now"), self)
        self.act_backup.triggered.connect(self.backup_now)
        self.act_restore = QAction(tr("&Restore…"), self)
        self.act_restore.triggered.connect(self.restore_backup)
        self.act_backup_folder = QAction(tr("Open the &backup folder"), self)
        self.act_backup_folder.setToolTip(tr("Save the data, then copy the backup file to another PC and use Restore there."))
        self.act_backup_folder.triggered.connect(self.open_backup_folder)
        self.act_exit = QAction(tr("E&xit"), self)
        self.act_exit.setShortcut(QKeySequence("Alt+F4"))
        self.act_exit.triggered.connect(self.close)
        fm.addAction(self.act_import)
        fm.addSeparator()
        fm.addAction(self.act_export_xlsx)
        fm.addAction(self.act_export_pdf)
        fm.addAction(self.act_print)
        fm.addSeparator()
        fm.addAction(self.act_backup)
        fm.addAction(self.act_restore)
        fm.addAction(self.act_backup_folder)
        fm.addSeparator()
        fm.addAction(self.act_exit)
        em = mb.addMenu(tr("&Edit"))
        self.act_undo = self.ctx.stack.createUndoAction(self, tr("&Undo"))
        self.act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.act_redo = self.ctx.stack.createRedoAction(self, tr("&Redo"))
        self.act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        em.addAction(self.act_undo)
        em.addAction(self.act_redo)
        vm = mb.addMenu(tr("&View"))
        lm = vm.addMenu(tr("&Language"))
        self.lang_group = QActionGroup(self)
        self.lang_actions: dict[str, QAction] = {}
        for code, label in (("en", "English"), ("ar", "العربية")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(i18n.lang() == code)
            a.triggered.connect(lambda _c=False, c=code: self.switch_language(c))
            self.lang_group.addAction(a)
            lm.addAction(a)
            self.lang_actions[code] = a
        hm = mb.addMenu(tr("&Help"))
        self.act_guide = QAction(tr("&User guide"), self)
        self.act_guide.setShortcut(QKeySequence("F1"))
        self.act_guide.triggered.connect(self.show_user_guide)
        self.act_about = QAction(tr("&About Roster Builder"), self)
        self.act_about.triggered.connect(self.show_about)
        hm.addAction(self.act_guide)
        hm.addSeparator()
        hm.addAction(self.act_about)

    def show_user_guide(self) -> UserGuideDialog:
        dlg = UserGuideDialog(self)
        self.last_help_dialog = dlg
        dlg.exec()
        return dlg

    def show_about(self) -> AboutDialog:
        dlg = AboutDialog(self, self.ctx.store.path)
        self.last_help_dialog = dlg
        dlg.exec()
        return dlg

    def open_backup_folder(self) -> None:
        folder = Path(self.ctx.store.path).parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ------------------------------------------------------------------ language
    def switch_language(self, code: str) -> bool:
        """Show the whole app in ``code`` ("en" / "ar") right now: the window is rebuilt around the same store, month,
        undo history and page; nothing is lost.  Refused while a build runs."""
        from PySide6.QtWidgets import QApplication

        if code == i18n.lang():
            return False
        if self.build.running:
            info_box(self, tr("Roster Builder"), tr("A build is running. Wait for it to finish or cancel it, then change the language."))
            if code in self.lang_actions:
                self.lang_actions[i18n.lang()].setChecked(True)
            return False
        self.flush_pages()
        page, geom = self.current_key(), self.saveGeometry()
        i18n.save_language(self.qsettings, code)
        app = QApplication.instance()
        i18n.apply_language(app, code)
        ctx = self.ctx
        for sig in (ctx.changed, ctx.month_changed, ctx.saved, ctx.message, ctx.request_new_month, ctx.request_delete_month,
                    ctx.request_reopen_month, ctx.request_jump, ctx.request_open_page, ctx.request_rebuild):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        new = MainWindow(ctx.store, self.qsettings, ctx=ctx)
        new.restoreGeometry(geom)
        if page in new._nav_index:
            new.go(page)
        new.show()
        self.successor = new
        app.main_window = new  # keeps the new window alive
        self.close()  # saves nothing new (geometry was written by the new window's own close later)
        self.deleteLater()
        return True

    def undo(self) -> None:
        self.flush_pages()
        self.ctx.stack.undo()

    def redo(self) -> None:
        self.flush_pages()
        self.ctx.stack.redo()

    def backup_now(self) -> Path | None:
        self.flush_pages()
        try:
            p = self.ctx.store.backup()
        except StoreError as exc:
            self._on_message(tr_msg(str(exc)), "error")
            return None
        self.statusBar().showMessage(tr('Backup saved: {p}', p=p) + "  " + tr("(copy this file to another PC and use File → Restore)"), 12000)
        return p

    def restore_backup(self, path: str | None = None) -> bool:
        st = self.ctx.store
        if st.path == ":memory:":
            self._on_message(tr("A temporary database cannot be restored."), "error")
            return False
        if not path:
            folder = str(Path(st.path).parent / "backups")
            path, _f = QFileDialog.getOpenFileName(self, tr("Restore a backup"), folder, tr("Roster backups (*.db)"))
            if not path:
                return False
        if not self.ctx.confirm(tr("Replace ALL current data with this backup? A backup of the current data is made first.")):
            return False
        self.flush_pages()
        try:
            st.backup()
            db_path = st.path
            st.close()
            new = Store.restore(path, db_path)
        except Exception as exc:  # noqa: BLE001 - the user gets a sentence, not a traceback
            self._on_message(tr('The backup could not be restored: {exc}', exc=tr_msg(str(exc))), "error")
            self.ctx.store = Store(st.path)
            return False
        self.ctx.set_store(new)
        self._on_saved(tr("Backup restored"))
        return True

    # ------------------------------------------------------------------ status
    def _on_saved(self, text: str = "Saved") -> None:
        from datetime import datetime

        self.saved_label.setText(tr('Saved ✓  {v0:%H:%M:%S}', v0=datetime.now()))
        self.statusBar().showMessage(tr(text), 3000)

    def _on_message(self, text: str, level: str = "info") -> None:
        self.statusBar().showMessage(text, 10000)
        if level == "error":
            self.saved_label.setText(tr("Not saved"))

    # ------------------------------------------------------------------ lifecycle
    def flush_pages(self) -> None:
        for p in self.pages.values():
            if hasattr(p, "flush"):
                p.flush()

    def _restore_geometry(self) -> None:
        g = self.qsettings.value("window/geometry")
        if isinstance(g, (QByteArray, bytes)) and not QByteArray(g).isEmpty():
            self.restoreGeometry(QByteArray(g))
        else:
            self.resize(1366, 768)

    def closeEvent(self, ev) -> None:  # noqa: N802
        self.build.shutdown()
        self.flush_pages()
        self.qsettings.setValue("window/geometry", self.saveGeometry())
        self.qsettings.sync()
        super().closeEvent(ev)
