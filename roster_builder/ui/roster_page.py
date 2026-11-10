"""Roster page: the colour-coded month grid (days x employees), click-to-edit with instant re-check, pinned edits,
Problems panel, coverage strip, undo/redo, zoom, export and print."""

from __future__ import annotations

import time
from datetime import date

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QColor, QKeySequence, QPainter, QPixmap, QShortcut, QUndoCommand
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
                               QScrollArea, QSlider, QSplitter, QStackedWidget, QTableView, QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import count_phrase, tr, tr_msg
from .. import app as engine
from ..report import build_cells
from ..roster_edit import CellState, RosterEditor, coverage_strip, problems_from_report
from .base import Page
from .roster_grid import (LEGEND, FILL, CellEditDialog, Col, CoverageStrip, DayHeader, GroupHeader, RosterDelegate,
                          RosterModel)

BASE_COL_W = 108
ZOOM_MIN, ZOOM_MAX = 0.5, 1.6
ALL_KINDS = frozenset({"settings", "reception", "target", "shift", "employee", "leave", "fixed", "demand"})


def _up(s) -> str:
    return (s or "").strip().upper()


class _CellCmd(QUndoCommand):
    """One roster edit (change, unlock, revert) on the shared Edit-menu undo stack."""

    def __init__(self, page: "RosterPage", key: str, d: date, before: CellState, after: CellState, text: str,
                 forget_origin: bool = False) -> None:
        super().__init__(text)
        self.page, self.key, self.d, self.before, self.after = page, key, d, before, after
        self.gen = page.generation
        self.forget_origin = forget_origin

    def redo(self) -> None:
        if self.page.generation == self.gen:
            self.page._set_cell(self.key, self.d, self.after, self.forget_origin)

    def undo(self) -> None:
        if self.page.generation == self.gen:
            self.page._set_cell(self.key, self.d, self.before)


class ProblemRow(QFrame):
    """One line of the Problems panel: the sentence (wrapped); a click shows the cell."""

    clicked = Signal(object)

    def __init__(self, problem) -> None:
        super().__init__()
        self.problem = problem
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bg, hov = {"warning": ("#FFFBEB", "#FEF3C7"), "info": ("#EFF6FF", "#DBEAFE")}.get(
            getattr(problem, "severity", "error"), ("#FEF2F2", "#FEE2E2"))
        self.setStyleSheet(f"QFrame {{ background: {bg}; border-radius: 4px; }} QFrame:hover {{ background: {hov}; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
        self.label = QLabel(tr_msg(problem.text))
        self.label.setWordWrap(True)
        self.label.setStyleSheet("background: transparent; font-size: 9pt;")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        h.addWidget(self.label)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self.clicked.emit(self.problem)
        super().mousePressEvent(ev)


class RosterView(QTableView):
    zoom_step = Signal(int)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_step.emit(1 if ev.angleDelta().y() > 0 else -1)
            ev.accept()
            return
        super().wheelEvent(ev)


class RosterPage(Page):
    key = "roster"
    title = "Roster"
    subtitle = ""
    watches = ALL_KINDS

    problems_changed = Signal(int)

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.editor: RosterEditor | None = None
        self.generation = 0
        self._built_at = None
        self._ym = None
        self.problems: list = []
        self.strip_data: dict = {}
        self.zoom = 1.0
        self.last_check_seconds = 0.0
        self.last_dialog = None
        self.report = None

        self.head.hide()  # the grid needs the height; the sidebar already says where we are
        self.sub.hide()
        self.layout().setContentsMargins(16, 8, 16, 8)
        self.pages = QStackedWidget()
        self.body.addWidget(self.pages, 1)
        # ---- empty state
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        self.empty_label = QLabel(tr("There is no roster for this month yet."))
        self.empty_label.setProperty("role", "group")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel(tr("Build it on the Build page – it takes about a minute."))
        hint.setProperty("role", "subtitle")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        go = QPushButton(tr("Go to Build"))
        go.setProperty("primary", True)
        go.clicked.connect(lambda: self.ctx.request_open_page.emit("build"))
        el.addWidget(self.empty_label)
        el.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(go)
        row.addStretch(1)
        el.addLayout(row)
        el.addStretch(2)
        self.pages.addWidget(empty)
        # ---- the roster
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        cv.addLayout(self._toolbar())
        cv.addLayout(self._legend())
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(True)
        self.split.addWidget(self._grid_widget())
        self.split.addWidget(self._problems_widget())
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.split.setSizes([860, 250])
        cv.addWidget(self.split, 1)
        self.strip = CoverageStrip()
        self.strip.day_clicked.connect(self.select_day)
        cv.addWidget(self.strip)
        self.pages.addWidget(content)

        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self.zoom_by(1), context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.zoom_by(1), context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.zoom_by(-1), context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.set_zoom(1.0), context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.set_zoom(1.0)

    # ------------------------------------------------------------------ building the widgets
    def _toolbar(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(6)

        def btn(text, tip, slot, primary=False):
            b = QPushButton(text)
            b.setToolTip(tip)
            if primary:
                b.setProperty("primary", True)
            b.clicked.connect(slot)
            h.addWidget(b)
            return b

        self.excel_btn = btn(tr("Export Excel…"), tr("Save this roster, with your changes, as an Excel workbook."), lambda: self.export_excel())
        self.pdf_btn = btn(tr("Export PDF…"), tr("Save this roster, with your changes, as a PDF."), lambda: self.export_pdf())
        self.print_btn = btn(tr("Print…"), tr("Print the roster on paper."), lambda: self.print_roster())
        h.addSpacing(10)
        self.rebuild_btn = btn(tr("Rebuild keeping my changes"), tr("Build the month again. Pinned cells stay exactly as you set them."),
                               lambda: self.ctx.request_rebuild.emit(True), primary=True)
        self.final_btn = btn(tr("Mark as final"), tr("Lock this month's inputs. You can re-open the month later."), lambda: self.mark_final())
        h.addStretch(1)
        # slim "Problems" header: always one short line; the list opens only when there is something to show
        self.prob_toggle = QPushButton(tr("Problems 0 ✓"))
        self.prob_toggle.setFlat(True)
        self.prob_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prob_toggle.clicked.connect(self._toggle_problems)
        h.addWidget(self.prob_toggle)
        self.zoom_out = QPushButton("−")
        self.zoom_out.setProperty("navbtn", True)
        self.zoom_out.setToolTip(tr("Zoom out (Ctrl + minus)"))
        self.zoom_out.clicked.connect(lambda: self.zoom_by(-1))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(int(ZOOM_MIN * 100), int(ZOOM_MAX * 100))
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(96)
        self.zoom_slider.setToolTip(tr("Zoom"))
        self.zoom_slider.valueChanged.connect(lambda v: self.set_zoom(v / 100.0, from_slider=True))
        self.zoom_in = QPushButton("+")
        self.zoom_in.setProperty("navbtn", True)
        self.zoom_in.setToolTip(tr("Zoom in (Ctrl + plus)"))
        self.zoom_in.clicked.connect(lambda: self.zoom_by(1))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(40)
        self.fit_btn = QPushButton(tr("Fit width"))
        self.fit_btn.setToolTip(tr("Zoom so that every employee fits on the screen."))
        self.fit_btn.clicked.connect(self.fit_width)
        for w in (self.zoom_out, self.zoom_slider, self.zoom_in, self.zoom_label, self.fit_btn):
            h.addWidget(w)
        return h

    def _legend(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(12)
        lab = QLabel(tr("Legend"))
        lab.setProperty("role", "hint")
        h.addWidget(lab)
        for kind, text in LEGEND:
            sw = QLabel()
            sw.setFixedSize(16, 12)
            sw.setStyleSheet(f"background: {FILL[kind]}; border: 1px solid #9CA3AF;")
            t = QLabel(tr(text))
            t.setProperty("role", "hint")
            h.addWidget(sw)
            h.addWidget(t)
        pin = QLabel(tr("📌 pinned"))
        pin.setProperty("role", "hint")
        red = QLabel(tr("▌ breaks a rule"))
        red.setStyleSheet("color: #DC2626; font-size: 9pt;")
        note = QLabel(tr("◤ manual note"))
        note.setStyleSheet("color: #D97706; font-size: 9pt;")
        note.setToolTip(tr("A change you made by hand that breaks no hard rule: overtime on a normal working day (orange) "
                        "or an extra day off (blue). Hover the cell for details."))
        h.addWidget(pin)
        h.addWidget(red)
        h.addWidget(note)
        h.addStretch(1)
        self.legend_row = h
        return h

    def _grid_widget(self) -> QWidget:
        self.model = RosterModel()
        self.view = RosterView()
        self.view.setModel(self.model)
        self.hh = GroupHeader(self.view)
        self.vh = DayHeader(self.view)
        self.view.setHorizontalHeader(self.hh)
        self.view.setVerticalHeader(self.vh)
        self.delegate = RosterDelegate(self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setShowGrid(False)
        self.view.setWordWrap(True)
        self.view.setMouseTracking(True)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.setStyleSheet("QTableView { background: white; border: 1px solid #CBD2DC; }")
        self.hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.view.doubleClicked.connect(self._on_activate)
        self.view.activated.connect(self._on_activate)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.zoom_step.connect(self.zoom_by)
        return self.view

    def _problems_widget(self) -> QWidget:
        box = QFrame()
        box.setObjectName("card")
        box.setMinimumWidth(180)
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        top = QHBoxLayout()
        t = QLabel(tr("Problems"))
        t.setProperty("role", "group")
        self.problem_badge = QLabel("0")
        self.problem_badge.setProperty("role", "badge-ok")
        top.addWidget(t)
        top.addWidget(self.problem_badge)
        top.addStretch(1)
        v.addLayout(top)
        self.problem_hint = QLabel("")
        self.problem_hint.setWordWrap(True)
        self.problem_hint.setProperty("role", "ok")
        v.addWidget(self.problem_hint)
        self.prob_scroll = QScrollArea()
        self.prob_scroll.setWidgetResizable(True)
        self.prob_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prob_scroll.setStyleSheet("QScrollArea { background: white; border: 1px solid #CBD2DC; border-radius: 6px; }")
        host = QWidget()
        host.setStyleSheet("background: white;")
        self.prob_col = QVBoxLayout(host)
        self.prob_col.setContentsMargins(6, 6, 6, 6)
        self.prob_col.setSpacing(2)
        self.prob_col.addStretch(1)
        self.prob_scroll.setWidget(host)
        v.addWidget(self.prob_scroll, 1)
        self.prob_rows: list[ProblemRow] = []
        self.prob_heads: list[QLabel] = []
        self.prob_box = box
        self._prob_user_hidden = False
        return box

    # ------------------------------------------------------------------ loading
    def apply_read_only(self, ro: bool) -> None:  # a built month's roster can still be edited (edits are the point)
        pass

    def reload(self) -> None:
        mi = self.ctx.month_info
        if mi is None or self.ctx.store.get_roster(mi.key) is None:
            self.editor = None
            self.report = None
            self.problems = []
            self.pages.setCurrentIndex(0)
            self.empty_label.setText(tr("There is no roster for this month yet.") if mi else tr("There is no month yet."))
            self.problems_changed.emit(0)
            return
        keep = self.selected_cell()
        if self._ym != mi.key or self._built_at != mi.built_at:
            self.generation += 1
        self._ym, self._built_at = mi.key, mi.built_at
        self.editor = RosterEditor(self.ctx.store, mi.key)
        self.pages.setCurrentIndex(1)
        self._show_buttons(mi)
        self._refresh_all(reset=True)
        if keep:
            self.select_cell(*keep)

    def _show_buttons(self, mi) -> None:
        self.final_btn.setVisible(mi.status == "draft")

    def _columns(self) -> list[Col]:
        inp = self.editor.inp
        order = {_up(r.name): i for i, r in enumerate(inp.receptions)}
        emps = [e for e in inp.employees if e.name.strip() and e.active and e.home]
        emps.sort(key=lambda e: order.get(_up(e.home), 99))  # stable: list order inside a reception
        return [Col(e.key, e.name, _up(e.gender), _up(e.home), e.uid) for e in emps]

    def _refresh_all(self, reset: bool = False) -> None:
        ed = self.editor
        t0 = time.perf_counter()
        self.report = ed.check()
        self.last_check_seconds = time.perf_counter() - t0
        self.problems = problems_from_report(ed, self.report)
        cells = build_cells(ed.inp, ed.roster)
        locked = {(self._key_of(u), date.fromisoformat(dt)) for (u, dt) in ed.locks}
        marks: dict = {}
        notes: dict = {}
        for p in self.problems:
            for c in p.cells:
                if p.severity == "error":
                    marks.setdefault(c, []).append(tr_msg(p.text))
                else:
                    notes.setdefault(c, []).append((p.severity, tr_msg(p.text)))
        self.model.notes = notes
        st = ed.inp.settings
        first = st.first_day
        days = [date.fromordinal(first.toordinal() + i) for i in range(st.days_in_month)]
        weekend = {d for d in days if st.is_weekend(d)}
        cols = self._columns()
        same = (not reset and [c.key for c in self.model.cols] == [c.key for c in cols] and self.model.days == days)
        if same:
            self.model.update_data(cells, locked, marks)
        else:
            self.model.set_data(cols, days, cells, locked, marks, weekend)
            self._apply_zoom_sizes()
        self.view.resizeRowsToContents()
        self.strip_data = coverage_strip(ed, self.report)
        self.strip.set_data(self.strip_data, days)
        self._fill_problems()
        self.problems_changed.emit(self.n_problems())

    def _key_of(self, uid: int) -> str:
        return next(e.key for e in self.editor.inp.employees if e.uid == uid)

    # ------------------------------------------------------------------ problems panel
    def _fill_problems(self) -> None:
        while self.prob_col.count() > 1:
            it = self.prob_col.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.prob_rows, self.prob_heads = [], []
        n = self.n_problems()
        self.problem_badge.setText(str(n))
        self.problem_badge.setProperty("role", "badge" if n else "badge-ok")
        self.problem_badge.style().unpolish(self.problem_badge)
        self.problem_badge.style().polish(self.problem_badge)
        self.problem_hint.setText(tr("✓  Every rule is kept.") if not n else tr("Click a line to see the cell."))
        self.problem_hint.setProperty("role", "ok" if not n else "hint")
        self.problem_hint.style().unpolish(self.problem_hint)
        self.problem_hint.style().polish(self.problem_hint)
        self.prob_scroll.setVisible(bool(self.problems))
        self._sync_problem_header()
        groups: dict = {}
        for p in self.problems:
            groups.setdefault(p.check, []).append(p)
        pos = 0
        for key, lst in groups.items():
            head = QLabel(f"{tr_msg(lst[0].check_name)}  ({len(lst)})")
            head.setWordWrap(True)
            head.setStyleSheet("font-weight: 600; padding: 6px 2px 2px 2px; background: white;")
            self.prob_col.insertWidget(pos, head)
            self.prob_heads.append(head)
            pos += 1
            for p in lst:
                row = ProblemRow(p)
                row.clicked.connect(self._on_problem_clicked)
                self.prob_col.insertWidget(pos, row)
                self.prob_rows.append(row)
                pos += 1

    def _sync_problem_header(self) -> None:
        """Slim header text and colour; the full list is shown only when there is something in it."""
        n = self.n_problems()
        w = sum(1 for p in self.problems if p.severity == "warning")
        total = len(self.problems)
        if not total:
            self._prob_user_hidden = False
        self.prob_box.setVisible(bool(total) and not self._prob_user_hidden)
        if n:
            txt, col = tr("Problems {n}", n=n), "#DC2626"
        elif w:
            txt, col = tr("Problems 0 ✓") + "  ·  " + count_phrase(w, "warning"), "#B45309"
        elif total:
            txt, col = tr("Problems 0 ✓") + "  ·  " + count_phrase(total, "note"), "#15803D"
        else:
            txt, col = tr("Problems 0 ✓"), "#15803D"
        if total:
            txt += ("  ◂" if not self.prob_box.isHidden() else "  ▸") if not i18n.is_rtl() else ("  ▸" if not self.prob_box.isHidden() else "  ◂")
        self.prob_toggle.setText(txt)
        bold = self.prob_toggle.font()
        bold.setBold(True)
        self.prob_toggle.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(txt) + 24)  # bold text is wider than the default hint
        self.prob_toggle.setStyleSheet(f"QPushButton {{ color: {col}; font-weight: 600; border: none; padding: 2px 6px; background: transparent; }}")
        self.prob_toggle.setToolTip(tr("Rule problems of the roster as it is now. Click to show or hide the list.") if total
                                    else tr("Every rule is kept."))
        # the count inside the list: red only for broken rules
        self.problem_badge.setProperty("role", "badge" if n else "badge-ok")
        for wdg in (self.problem_badge,):
            wdg.style().unpolish(wdg)
            wdg.style().polish(wdg)

    def _toggle_problems(self) -> None:
        if not self.problems:
            return
        self._prob_user_hidden = not self.prob_box.isHidden()
        self._sync_problem_header()

    def n_problems(self) -> int:
        """Broken rules only (manual-exception warnings and notes are listed but not counted here or in the sidebar badge)."""
        return sum(1 for p in self.problems if p.severity == "error")

    def problem_items(self) -> list[str]:
        """Texts of the problem lines (tests)."""
        return [r.problem.text for r in self.prob_rows]

    def problem_titles(self) -> list[str]:
        """Headings of the problem groups, with their counts (tests)."""
        return [h.text() for h in self.prob_heads]

    def _on_problem_clicked(self, p) -> None:
        if p.cells:
            self.select_cell(*p.cells[0])
        elif p.day:
            self.select_day(p.day)

    # ------------------------------------------------------------------ selection / jump
    def selected_cell(self):
        ix = self.view.currentIndex()
        if not ix.isValid() or not self.model.cols:
            return None
        return self.model.cols[ix.column()].key, self.model.days[ix.row()]

    def select_cell(self, emp_key: str, d: date) -> bool:
        c, r = self.model.col_of(_up(emp_key)), self.model.row_of(d)
        if c < 0 or r < 0:
            return False
        ix = self.model.index(r, c)
        self.view.setCurrentIndex(ix)
        self.view.scrollTo(ix, QAbstractItemView.ScrollHint.PositionAtCenter)
        return True

    def select_day(self, d: date) -> None:
        r = self.model.row_of(d)
        if r >= 0 and self.model.cols:
            col = self.view.currentIndex().column() if self.view.currentIndex().isValid() else 0
            ix = self.model.index(r, max(0, col))
            self.view.setCurrentIndex(ix)
            self.view.scrollTo(ix, QAbstractItemView.ScrollHint.PositionAtCenter)

    def focus_record(self, record_id, field: str, message: str = "") -> None:
        """Jump target of a conflict: employee uid + ISO date of the roster cell."""
        if self.editor is None or record_id is None:
            return
        try:
            d = date.fromisoformat(field)
        except (TypeError, ValueError):
            return
        self.select_cell(self._key_of(record_id), d)

    # ------------------------------------------------------------------ zoom
    def set_zoom(self, z: float, from_slider: bool = False) -> None:
        z = max(ZOOM_MIN, min(ZOOM_MAX, round(z, 2)))
        self.zoom = z
        self.zoom_label.setText(f"{int(round(z * 100))}%")
        if not from_slider:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(int(round(z * 100)))
            self.zoom_slider.blockSignals(False)
        self._apply_zoom_sizes()

    def zoom_by(self, steps: int) -> None:
        self.set_zoom(self.zoom + 0.1 * steps)

    def _apply_zoom_sizes(self) -> None:
        z = self.zoom
        self.delegate.zoom = z
        self.hh.zoom = z
        self.vh.zoom = z
        self.hh.setDefaultSectionSize(int(BASE_COL_W * z))
        for i in range(self.model.columnCount()):
            self.hh.resizeSection(i, int(BASE_COL_W * z))
        self.hh.setFixedHeight(self.hh.sizeHint().height())
        self.vh.setFixedWidth(self.vh.sizeHint().width())
        self.view.resizeRowsToContents()
        self.hh.viewport().update()
        self.vh.viewport().update()
        self.view.viewport().update()

    def fit_width(self) -> None:
        """Pick the zoom at which every employee column fits next to the day column."""
        n = max(1, self.model.columnCount())
        avail = self.view.viewport().width()
        self.set_zoom(max(ZOOM_MIN, min(1.0, avail / (n * BASE_COL_W))))

    # ------------------------------------------------------------------ editing
    def cell_text(self, emp_name: str, d: date) -> str:
        c = self.model.cells.get((emp_name, d))
        return c.text if c else ""

    def cell_kind(self, emp_name: str, d: date) -> str:
        c = self.model.cells.get((emp_name, d))
        return c.kind if c else "empty"

    def cell_problems(self, emp_name: str, d: date) -> list[str]:
        return list(self.model.problems.get((_up(emp_name), d), []))

    def is_pinned(self, emp_name: str, d: date) -> bool:
        return (_up(emp_name), d) in self.model.locked

    def _on_activate(self, ix) -> None:
        if ix.isValid():
            self.edit_cell(self.model.cols[ix.column()].key, self.model.days[ix.row()])

    def edit_cell(self, emp_key: str, d: date) -> bool:
        """Open the cell editor popup; returns True when a change was applied."""
        if self.editor is None:
            return False
        cell = self.model.cells.get((self.editor.inp.emp_by_key[_up(emp_key)].name, d))
        dlg = CellEditDialog(self.window(), self.editor, _up(emp_key), d, leave=bool(cell and cell.kind == "leave"))
        self.last_dialog = dlg
        if dlg.exec() != dlg.DialogCode.Accepted or dlg.leave:
            return False
        v = dlg.values()
        return self.apply_cell_edit(emp_key, d, v["shift"], v["rec"], v["ot"], v["eb"], v["ea"])

    def apply_cell_edit(self, emp_key: str, d: date, shift: str | None, rec: str | None = None, ot: bool = False,
                        eb: int = 0, ea: int = 0) -> bool:
        """Change a cell (shift code, or ``None`` = OFF), pin it, re-check, and put it on the undo stack."""
        ed = self.editor
        if ed is None:
            return False
        key = _up(emp_key)
        before = ed.state(key, d)
        after = ed.make_state(key, d, shift, rec, ot, eb, ea)
        if before.asg == after.asg and before.lock == after.lock and before.off == after.off:
            return False
        self.ctx.stack.push(_CellCmd(self, key, d, before, after, tr("Edit roster cell")))
        return True

    def unlock_cell(self, emp_key: str, d: date) -> bool:
        """Remove the pin of a cell; its value stays until the next rebuild."""
        ed = self.editor
        key = _up(emp_key)
        before = ed.state(key, d)
        if before.lock is None:
            return False
        self.ctx.stack.push(_CellCmd(self, key, d, before, CellState(before.asg, None, False), tr("Unlock roster cell")))
        return True

    def revert_cell(self, emp_key: str, d: date) -> bool:
        """Put the value of the build back (and remove the pin)."""
        ed = self.editor
        key = _up(emp_key)
        if not ed.can_revert(key, d):
            return False
        before = ed.state(key, d)
        self.ctx.stack.push(_CellCmd(self, key, d, before, ed.revert_state(key, d), tr("Revert roster cell"), forget_origin=True))
        return True

    def _set_cell(self, key: str, d: date, st: CellState, forget_origin: bool = False) -> None:
        ed = self.editor
        ed.set_state(key, d, st, persist=False)
        if forget_origin:
            ed.origin.pop(f"{ed.emp_uid(key)}|{d.isoformat()}", None)
        ed.persist()
        self._refresh_all()
        self.ctx.notify({"roster"}, self, tr("Roster change saved"))

    def _context_menu(self, pos) -> None:
        ix = self.view.indexAt(pos)
        if not ix.isValid() or self.editor is None:
            return
        key, d = self.model.cols[ix.column()].key, self.model.days[ix.row()]
        self.view.setCurrentIndex(ix)
        m = QMenu(self)
        a_edit = m.addAction(tr("Change…"))
        a_edit.triggered.connect(lambda: self.edit_cell(key, d))
        a_unlock = m.addAction(tr("Unlock (keep the value)"))
        a_unlock.setEnabled(self.editor.is_locked(key, d))
        a_unlock.triggered.connect(lambda: self.unlock_cell(key, d))
        a_rev = m.addAction(tr("Revert to built value"))
        a_rev.setEnabled(self.editor.can_revert(key, d))
        a_rev.triggered.connect(lambda: self.revert_cell(key, d))
        self.last_menu = m
        m.exec(self.view.viewport().mapToGlobal(pos))

    def context_actions(self, emp_key: str, d: date) -> dict[str, bool]:
        """Which context-menu entries are enabled for a cell (tests)."""
        return {"unlock": self.editor.is_locked(emp_key, d), "revert": self.editor.can_revert(emp_key, d)}

    # ------------------------------------------------------------------ month actions
    def mark_final(self) -> None:
        mi = self.ctx.month_info
        if mi is None or not self.ctx.confirm(tr("Mark this month as final? Its inputs are locked (you can re-open it later).")):
            return
        self.ctx.store.mark_built(mi.key)
        self.ctx.month_changed.emit()
        self.ctx.saved.emit(tr("Month marked as final"))

    def ask_save(self, title: str, default: str, filt: str) -> str:
        """Ask where to save (tests replace this)."""
        path, _f = QFileDialog.getSaveFileName(self, title, default, filt)
        return path

    def _default_name(self, ext: str) -> str:
        return f"roster_{self._ym[0]}-{self._ym[1]:02d}.{ext}" if self._ym else f"roster.{ext}"

    def export_excel(self, path: str | None = None):
        """Write the roster workbook (with the user's edits).  Returns the path or ``None`` when cancelled."""
        if self.editor is None:
            return None
        path = path or self.ask_save(tr("Export to Excel"), self._default_name("xlsx"), tr("Excel workbook (*.xlsx)"))
        if not path:
            return None
        try:
            engine.export_month(self.ctx.store, *self._ym, xlsx_path=path)
        except Exception as exc:  # noqa: BLE001 - friendly message, not a traceback
            self.show_message(tr('The Excel file could not be written: {exc}', exc=tr_msg(str(exc))))
            return None
        self.show_message("")
        self.ctx.saved.emit(tr("Exported {path}", path=path))
        return path

    def export_pdf(self, path: str | None = None):
        if self.editor is None:
            return None
        path = path or self.ask_save(tr("Export to PDF"), self._default_name("pdf"), tr("PDF (*.pdf)"))
        if not path:
            return None
        try:
            engine.export_month(self.ctx.store, *self._ym, pdf_path=path)
        except Exception as exc:  # noqa: BLE001
            self.show_message(tr('The PDF could not be written: {exc}', exc=tr_msg(str(exc))))
            return None
        self.show_message("")
        self.ctx.saved.emit(tr("Exported {path}", path=path))
        return path

    # ------------------------------------------------------------------ printing
    def render_full(self) -> QPixmap:
        """The whole grid (headers and every day) as one picture."""
        tmp = RosterView()
        tmp.setModel(self.model)
        hh, vh = GroupHeader(tmp), DayHeader(tmp)
        hh.zoom = vh.zoom = self.zoom
        tmp.setHorizontalHeader(hh)
        tmp.setVerticalHeader(vh)
        dl = RosterDelegate(tmp)
        dl.zoom = self.zoom
        tmp.setItemDelegate(dl)
        tmp.setShowGrid(False)
        tmp.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tmp.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for i in range(self.model.columnCount()):
            hh.resizeSection(i, self.hh.sectionSize(i))
        hh.setFixedHeight(self.hh.height())
        vh.setFixedWidth(self.vh.width())
        tmp.resizeRowsToContents()
        w = vh.width() + sum(hh.sectionSize(i) for i in range(self.model.columnCount())) + 2
        h = hh.height() + sum(vh.sectionSize(i) for i in range(self.model.rowCount())) + 2
        tmp.resize(w, h)
        tmp.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        tmp.show()
        pm = tmp.grab()
        tmp.hide()
        tmp.deleteLater()
        return pm

    def render_to_printer(self, printer) -> int:
        """Paint the roster on ``printer`` (landscape, fit to page width, header repeated on every page).  Returns the page count."""
        from PySide6.QtGui import QPageLayout

        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        pm = self.render_full()
        painter = QPainter(printer)
        page = printer.pageRect(printer.Unit.DevicePixel)
        title_h = int(page.height() * 0.05)
        scale = page.width() / pm.width()
        head_px = max(1, self.hh.height())
        body_h = int((page.height() - title_h) / scale) - head_px
        pages = 0
        y = head_px
        title = tr("Roster {v0}-{v1:02d}", v0=self._ym[0], v1=self._ym[1]) if self._ym else tr("Roster")
        while y < pm.height():
            if pages:
                printer.newPage()
            painter.save()
            painter.setPen(QColor("black"))
            f = painter.font()
            f.setPointSize(max(8, int(title_h / 3.2)))
            painter.setFont(f)
            painter.drawText(QRect(0, 0, int(page.width()), title_h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), title)
            painter.translate(0, title_h)
            painter.scale(scale, scale)
            painter.drawPixmap(0, 0, pm, 0, 0, pm.width(), head_px)
            painter.drawPixmap(0, head_px, pm, 0, y, pm.width(), min(body_h, pm.height() - y))
            painter.restore()
            y += body_h
            pages += 1
        painter.end()
        return pages

    def print_roster(self, printer=None) -> bool:
        """Print through the system print dialog (or straight to ``printer`` when given)."""
        if self.editor is None:
            return False
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        if printer is None:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            dlg = QPrintDialog(printer, self)
            if dlg.exec() != dlg.DialogCode.Accepted:
                return False
        self.render_to_printer(printer)
        return True


__all__ = ["RosterPage"]
