"""Month screens (the sidebar group "This month"): overview, leave, fixed shifts and demand rows.

Only a *draft* month can be edited; a built month shows the same tables read-only (the window shows the
banner).  Every edit is written to the store at once through :class:`~roster_builder.ui.context.AppContext`
(auto-save + undo).  Rows with a problem get a red or amber border and a tooltip; a row that cannot be saved
at all (leave that ends before it starts) is simply not saved until it is fixed.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from PySide6.QtCore import QDate, QEvent, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import disp_code, month_name, strf, tr, tr_msg, ym_title
from ..store import StoreError
from . import theme
from .base import Page
from .widgets import WEEKDAYS, no_wheel, set_state

LEAVE_TYPES = ["Annual", "Sick", "Iqama/Admin", "Other"]


def kind_value(shown: str) -> str:
    """The leave type as stored: the preset's English name when the user picked a (translated) preset, else the typed text."""
    t = (shown or "").strip()
    for k in LEAVE_TYPES:
        if t == tr(k):
            return k
    return t or "Annual"


def weekday_name(d: date) -> str:
    """SUN..SAT name of a date."""
    return WEEKDAYS[(d.weekday() + 1) % 7]


def qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def pydate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


def month_bounds(ym) -> tuple[date, date]:
    y, m = ym
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def month_title(ym) -> str:
    return ym_title(ym[0], ym[1])


def fmt(d: date) -> str:
    return f"{strf(d, '%a')} {d.day} {strf(d, '%b')}"


# ----------------------------------------------------------------------------- New month dialog
class NewMonthDialog(QDialog):
    """Ask which month to create and whether the fixed shifts of the latest earlier month are copied."""

    def __init__(self, parent, year: int, month: int, has_previous: bool, existing=()) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New month"))
        self.existing = {tuple(x) for x in existing}
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lead = QLabel(tr("Which month do you want to plan?"))
        lead.setProperty("role", "group")
        lay.addWidget(lead)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self.month = QComboBox()
        for m in range(1, 13):
            self.month.addItem(month_name(m), m)
        self.month.setCurrentIndex(month - 1)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(year)
        self.year.setFixedWidth(120)
        form.addRow(tr("Month"), self.month)
        form.addRow(tr("Year"), self.year)
        lay.addLayout(form)
        self.copy_fixed = QCheckBox(tr("Copy the fixed shifts of the previous month (same day of the month)"))
        self.copy_fixed.setToolTip(tr("Fixed shifts are placed exactly as entered. Tick this only when the same people "
                                   "work the same shifts on the same dates again."))
        self.copy_fixed.setEnabled(has_previous)
        lay.addWidget(self.copy_fixed)
        note = QLabel(tr("<b>What the new month starts with</b><br>"
                      "• Staff, shifts, coverage and settings come from your <b>current setup</b> (not from the previous month).<br>"
                      "• Leave that overlaps the new month is carried over.<br>")
                      + (tr("• Demand rows are copied from the previous month.<br>") if has_previous else "")
                      + tr("• Everything can still be changed afterwards. Months you already built stay as they are."))
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setProperty("role", "hint")
        lay.addWidget(note)
        self.problem = QLabel("")
        self.problem.setProperty("role", "error")
        self.problem.hide()
        lay.addWidget(self.problem)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(tr("Create month"))
        self.ok_btn.setProperty("primary", True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.month.currentIndexChanged.connect(self._check)
        self.year.valueChanged.connect(self._check)
        self._check()

    def value(self) -> tuple[int, int]:
        return int(self.year.value()), int(self.month.currentData())

    def copy_fixed_value(self) -> bool:
        return self.copy_fixed.isEnabled() and self.copy_fixed.isChecked()

    def _check(self, *_a) -> None:
        taken = self.value() in self.existing
        self.problem.setText(tr('{v0} already exists.', v0=month_title(self.value())) if taken else "")
        self.problem.setVisible(taken)
        self.ok_btn.setEnabled(not taken)


# ----------------------------------------------------------------------------- table page base
class RowBase:
    """The widgets of one table row."""

    def __init__(self, uid: int) -> None:
        self.uid = uid
        self.msg = QLabel("")
        self.msg.setWordWrap(True)

    def widgets(self) -> list[QWidget]:
        return []

    def field_widgets(self, field: str) -> list[QWidget]:
        return []


def host(*ws: QWidget, margins=(8, 4, 8, 4), stretch: bool = True) -> QWidget:
    h = QWidget()
    lay = QHBoxLayout(h)
    lay.setContentsMargins(*margins)
    lay.setSpacing(6)
    for w in ws:
        lay.addWidget(w)
    if stretch:
        lay.addStretch(1)
    return h


class MonthTablePage(Page):
    """Table of month records with Add / Delete, row focus tracking, read-only mode and jump support."""

    kind = ""
    COLS: list[str] = []
    ROW_H = 46
    add_label = "Add"
    empty_text = ""

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.rows: dict[int, RowBase] = {}
        bar = QHBoxLayout()
        self.add_btn = QPushButton(tr(self.add_label))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self.add_row)
        self.del_btn = QPushButton(tr("Delete"))
        self.del_btn.setProperty("danger", True)
        self.del_btn.setToolTip(tr("Delete the selected row."))
        self.del_btn.clicked.connect(self.delete_row)
        bar.addWidget(self.add_btn)
        bar.addWidget(self.del_btn)
        bar.addSpacing(12)
        self.month_hint = QLabel("")
        self.month_hint.setProperty("role", "hint")
        bar.addWidget(self.month_hint)
        bar.addStretch(1)
        self.body.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.ROW_H)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setMinimumHeight(140)
        self.table.setStyleSheet("QTableWidget::item { padding: 0px; }")  # cell widgets get the whole cell
        self.table.itemSelectionChanged.connect(self._sel_changed)
        self.configure_columns(self.table.horizontalHeader())
        self.body.addWidget(self.table, 1)
        self.empty = QLabel(tr(self.empty_text))
        self.empty.setProperty("role", "hint")
        self.empty.setWordWrap(True)
        self.empty.hide()
        self.body.addWidget(self.empty)
        QApplication.instance().focusChanged.connect(self._focus_moved)

    # ---- to override
    def configure_columns(self, hh: QHeaderView) -> None: ...
    def records(self) -> list: return []
    def make_row(self, rec) -> RowBase: raise NotImplementedError
    def new_fields(self) -> dict | None: raise NotImplementedError
    def field_widgets(self, row: RowBase, field: str) -> list[QWidget]: return row.field_widgets(field)
    def validate_rows(self) -> None: ...
    def after_reload(self) -> None: ...

    # ---- building
    def can_edit(self) -> bool:
        return bool(self.ctx.month_info) and not self.ctx.read_only

    def reload(self) -> None:
        sel = self.selected_uid()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.rows.clear()
        mi = self.ctx.month_info
        self.month_hint.setText(tr('for {v0}', v0=month_title(mi.key)) if mi else "")
        if mi:
            for rec in self.records():
                self.append_row(rec)
        self.table.blockSignals(False)
        if sel in self.rows:
            self.table.selectRow(list(self.rows).index(sel))
        self.empty.setText(tr(self.empty_text) if mi else tr("There is no month yet. Click 'New month' at the top to create one."))
        self.empty.setVisible(not self.rows)
        self.validate_rows()
        self.after_reload()

    def append_row(self, rec) -> RowBase:
        row = self.make_row(rec)
        self.rows[rec.uid] = row
        i = self.table.rowCount()
        self.table.insertRow(i)
        self.place(i, row)
        return row

    def place(self, i: int, row: RowBase) -> None: ...

    def selected_uid(self) -> int | None:
        i = self.table.currentRow()
        keys = list(self.rows)
        return keys[i] if 0 <= i < len(keys) else None

    def select(self, uid: int) -> None:
        if uid in self.rows:
            i = list(self.rows).index(uid)
            self.table.selectRow(i)
            self.table.scrollToItem(self.table.item(i, 0))

    def _sel_changed(self) -> None:
        self.del_btn.setEnabled(self.can_edit() and self.selected_uid() is not None)

    def _focus_moved(self, _old, new) -> None:
        """Clicking or tabbing into a cell widget selects its row (so Delete knows which one)."""
        if new is None or not isinstance(new, QWidget) or not self.table.viewport().isAncestorOf(new):
            return
        pos = new.mapTo(self.table.viewport(), QPoint(2, 2))
        i = self.table.rowAt(pos.y())
        if i >= 0 and i != self.table.currentRow():
            self.table.selectRow(i)

    # ---- editing
    def add_row(self) -> None:
        if not self.can_edit():
            return
        fields = self.new_fields()
        if fields is None:
            return
        try:
            rec = self.ctx.add(self.kind, origin=self, **fields)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self.show_message("")
        self.append_row(self.ctx.view().get(self.kind, rec.uid))
        self.empty.hide()
        self.select(rec.uid)
        self.validate_rows()
        self.after_reload()

    def delete_row(self) -> None:
        uid = self.selected_uid()
        if uid is None or not self.can_edit():
            return
        try:
            self.ctx.delete(self.kind, uid, origin=self)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self.show_message("")
        self.reload()

    def write(self, uid: int, **fields) -> bool:
        if self._loading:
            return False
        try:
            self.ctx.update(self.kind, uid, origin=self, **fields)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return False
        self.show_message("")
        self.validate_rows()
        self.after_reload()
        return True

    def apply_read_only(self, ro: bool) -> None:
        editable = self.can_edit()
        self.add_btn.setEnabled(editable)
        self.del_btn.setEnabled(editable and self.selected_uid() is not None)
        for row in self.rows.values():
            for w in row.widgets():
                w.setEnabled(editable)

    # ---- jump from the Input check panel
    def focus_record(self, record_id, field: str, message: str = "") -> None:
        if record_id not in self.rows:
            return
        self.select(record_id)
        row = self.rows[record_id]
        ws = self.field_widgets(row, field) or row.widgets()[:1]
        for w in ws:
            set_state(w, message or "Please check this field.")
        if ws:
            self.table.scrollToItem(self.table.item(list(self.rows).index(record_id), 0), QAbstractItemView.ScrollHint.PositionAtCenter)

    # ---- helpers for subclasses
    def employees(self):
        return self.ctx.view().list_employees()

    def emp_combo(self, uid: int | None) -> QComboBox:
        c = QComboBox()
        c.setMinimumWidth(160)
        for e in self.employees():
            c.addItem(e.name + ("" if e.active else tr(" (inactive)")), e.uid)
        c.setCurrentIndex(max(c.findData(uid), 0))
        no_wheel(c)
        return c

    def date_edit(self, d: date | None, lo: date | None = None, hi: date | None = None) -> QDateEdit:
        w = QDateEdit()
        w.setCalendarPopup(True)
        w.setDisplayFormat("ddd d MMM yyyy")
        w.setMinimumWidth(176)
        if lo and hi:
            w.setDateRange(qdate(lo), qdate(hi))
        else:
            w.setDateRange(QDate(2000, 1, 1), QDate(2100, 12, 31))
        if d:
            w.setDate(qdate(d))
        no_wheel(w)
        return w

    @staticmethod
    def set_msg(row: RowBase, text: str, level: str = "error") -> None:
        row.msg.setText(text)
        row.msg.setProperty("role", ("hint" if level == "info" else level) if text else "")
        row.msg.setToolTip(text)
        row.msg.style().unpolish(row.msg)
        row.msg.style().polish(row.msg)


# ----------------------------------------------------------------------------- leave
class LeaveRow(RowBase):
    def __init__(self, page: "LeavePage", rec) -> None:
        super().__init__(rec.uid)
        self.emp = page.emp_combo(rec.emp_uid)
        self.frm = page.date_edit(rec.from_date)
        self.to = page.date_edit(rec.to_date)
        self.kind = QComboBox()
        self.kind.setEditable(True)
        self.kind.addItems([tr(x) for x in LEAVE_TYPES])
        self.kind.setMinimumWidth(170)
        self.kind.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        no_wheel(self.kind)
        self.kind.lineEdit().setMaxLength(30)
        text = tr(rec.kind or "Annual")
        i = self.kind.findText(text, Qt.MatchFlag.MatchFixedString)
        if i >= 0:
            self.kind.setCurrentIndex(i)
        else:
            self.kind.setEditText(text)
        self.emp.setToolTip(tr("Who is on leave."))
        self.frm.setToolTip(tr("First day of the leave. Click the arrow to pick it from a calendar."))
        self.to.setToolTip(tr("Last day of the leave (this day is included)."))
        self.kind.setToolTip(tr("The kind of leave. Pick one or type your own."))

    def widgets(self):
        return [self.emp, self.frm, self.to, self.kind]

    def field_widgets(self, field):
        return {"emp_uid": [self.emp], "from_date": [self.frm], "to_date": [self.to], "kind": [self.kind]}.get(field, [])


class LeaveStrip(QWidget):
    """Compact month calendar: one thin row per employee with leave, a coloured cell for each leave day."""

    ROW = 20
    NAME_W = 150
    HEAD = 34

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.year, self.month = 2000, 1
        self.rows: list[tuple[str, dict[int, str]]] = []
        self.weekend: set[str] = {"FRI", "SAT"}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fit()

    def set_data(self, year: int, month: int, rows, weekend=()) -> None:
        self.year, self.month = year, month
        self.rows = list(rows)
        self.weekend = set(weekend) or {"FRI", "SAT"}
        self._fit()
        self.update()

    def _fit(self) -> None:
        self.setFixedHeight(self.HEAD + self.ROW * max(len(self.rows), 1) + 6)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(900, self.height())

    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def _x(self, d: int, cw: float) -> float:
        """Left edge of day ``d`` (1-based); the first day is at the right in a mirrored layout."""
        return self.width() - self.NAME_W - d * cw if i18n.is_rtl() else self.NAME_W + (d - 1) * cw

    def _cw(self) -> float:
        return max(14.0, (self.width() - self.NAME_W - 4) / self.days())

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = self.days()
        cw = self._cw()
        small = QFont(self.font())
        small.setPointSizeF(8.0)
        bold = QFont(small)
        bold.setBold(True)
        p.setFont(small)
        for d in range(1, n + 1):
            x = self._x(d, cw)
            wd = weekday_name(date(self.year, self.month, d))
            if wd in self.weekend:
                p.fillRect(QRectF(x, 0, cw, self.HEAD + self.ROW * max(len(self.rows), 1)), QColor("#E8EDF3"))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(x, 0, cw, 16), Qt.AlignmentFlag.AlignCenter, tr(wd)[0] if i18n.is_ar() else wd[0])
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(x, 15, cw, 16), Qt.AlignmentFlag.AlignCenter, str(d))
        if not self.rows:
            p.setPen(QColor(theme.MUTED))
            p.setFont(self.font())
            p.drawText(QRectF(8, self.HEAD, self.width() - 16, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       tr("Nobody is on leave in this month."))
            return
        for j, (name, days) in enumerate(self.rows):
            y = self.HEAD + j * self.ROW
            p.setFont(small)
            p.setPen(QColor(theme.TEXT))
            name_rect = QRectF(self.width() - self.NAME_W + 6, y, self.NAME_W - 10, self.ROW) if i18n.is_rtl() else QRectF(4, y, self.NAME_W - 10, self.ROW)
            p.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, self.NAME_W - 14))
            for d in range(1, n + 1):
                x = self._x(d, cw)
                if d in days:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor("#8B5CF6"))
                    p.drawRoundedRect(QRectF(x + 1, y + 3, cw - 2, self.ROW - 6), 3, 3)
                else:
                    p.setPen(QPen(QColor("#E2E8F0"), 1))
                    p.drawLine(int(x + cw / 2 - 1), int(y + self.ROW / 2), int(x + cw / 2 + 1), int(y + self.ROW / 2))

    def event(self, ev):
        if ev.type() == QEvent.Type.ToolTip:
            from PySide6.QtWidgets import QToolTip

            d = int(((self.width() - self.NAME_W - ev.pos().x()) if i18n.is_rtl() else (ev.pos().x() - self.NAME_W)) // self._cw()) + 1
            j = int((ev.pos().y() - self.HEAD) // self.ROW)
            if 1 <= d <= self.days() and 0 <= j < len(self.rows):
                name, days = self.rows[j]
                day = date(self.year, self.month, d)
                QToolTip.showText(ev.globalPos(), tr("{name}, {v0}", name=name, v0=fmt(day)) + (": " + tr(days[d]) if d in days else tr(": working")), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)


class LeavePage(MonthTablePage):
    key = "leave"
    kind = "leave"
    title = "Leave"
    subtitle = ("Who is away and when. Nobody is rostered on a leave day. The first and last day are both included. "
                "Leave that overlaps a new month is carried over automatically.")
    watches = frozenset({"leave", "employee", "settings"})
    COLS = ["Employee", "From", "To", "Type", "Check"]
    add_label = "Add leave"
    empty_text = "Nobody is on leave in this month. Click 'Add leave' to enter a holiday, sick leave or an Iqama / admin day."

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        cap = QLabel(tr("Leave calendar"))
        cap.setStyleSheet("font-weight: 600;")
        self.body.addWidget(cap)
        self.strip = LeaveStrip()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(self.strip)
        sa.setFixedHeight(150)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        self.strip_area = sa
        self.body.addWidget(sa)

    def configure_columns(self, hh) -> None:
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 220)
        for c, w in ((1, 206), (2, 206), (3, 200)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

    def records(self):
        return self.ctx.view().list_leave()

    def make_row(self, rec) -> LeaveRow:
        r = LeaveRow(self, rec)
        for w in (r.emp, r.frm, r.to):
            sig = w.currentIndexChanged if isinstance(w, QComboBox) else w.dateChanged
            sig.connect(lambda _v, row=r: self.commit(row))
        r.kind.currentTextChanged.connect(lambda _t, row=r: self.commit(row))
        return r

    def place(self, i: int, row: LeaveRow) -> None:
        for c, w in enumerate((row.emp, row.frm, row.to, row.kind)):
            self.table.setCellWidget(i, c, host(w))
        self.table.setCellWidget(i, 4, host(row.msg, stretch=False))

    def values(self, r: LeaveRow) -> dict:
        return {"emp_uid": r.emp.currentData(), "from_date": pydate(r.frm.date()), "to_date": pydate(r.to.date()),
                "kind": kind_value(r.kind.currentText())}

    def commit(self, r: LeaveRow) -> None:
        if self._loading:
            return
        v = self.values(r)
        if v["to_date"] < v["from_date"]:  # cannot be stored: show it, save once it is right
            self.validate_rows()
            return
        self.write(r.uid, **v)

    def new_fields(self):
        if not self.employees():
            self.show_message(tr("Add an employee first (Employees page)."))
            return None
        first, last = month_bounds(self.ctx.month)
        today = date.today()
        d = today if first <= today <= last else first
        busy = {(lv.emp_uid) for lv in self.ctx.view().list_leave() if lv.from_date <= d <= lv.to_date}
        emp = next((e.uid for e in self.employees() if e.uid not in busy and e.active), self.employees()[0].uid)
        return {"emp_uid": emp, "from_date": d, "to_date": d, "kind": "Annual"}

    def problems(self) -> dict[int, list[tuple[str, str, str]]]:
        """``{row uid: [(field, level, message)]}`` for the values currently shown."""
        first, last = month_bounds(self.ctx.month) if self.ctx.month else (None, None)
        out: dict[int, list] = {}
        vals = {uid: self.values(r) for uid, r in self.rows.items()}
        for uid, v in vals.items():
            probs = out.setdefault(uid, [])
            if v["to_date"] < v["from_date"]:
                probs.append(("to_date", "error", tr("The last day is before the first day.")))
                continue
            for u2, w in vals.items():
                if u2 < uid and w["emp_uid"] == v["emp_uid"] and w["to_date"] >= w["from_date"] \
                        and w["from_date"] <= v["to_date"] and v["from_date"] <= w["to_date"]:
                    probs.append(("from_date", "error", tr("Overlaps another leave ({v0} - {v1}).", v0=fmt(w["from_date"]), v1=fmt(w["to_date"]))))
                    break
            if first and (v["to_date"] < first or v["from_date"] > last):
                probs.append(("from_date", "warn", tr("Outside {v0}: no effect on this month.", v0=month_title(self.ctx.month))))
            elif first and (v["from_date"] < first or v["to_date"] > last):
                probs.append(("from_date", "info", tr("Continues outside {v0}.", v0=month_title(self.ctx.month))))
        return out

    def validate_rows(self) -> None:
        probs = self.problems()
        for uid, r in self.rows.items():
            for w in r.widgets():
                set_state(w, None)
            ps = probs.get(uid, [])
            worst = "error" if any(p[1] == "error" for p in ps) else "warn" if any(p[1] == "warn" for p in ps) else "info"
            for field, level, msg in ps:
                for w in r.field_widgets(field):
                    if level != "info" and set_state_level(w, level):
                        set_state(w, msg, level)
            self.set_msg(r, " ".join(dict.fromkeys(p[2] for p in ps)), worst)
        self.update_strip()

    def update_strip(self) -> None:
        if not self.ctx.month_info:
            self.strip.set_data(2000, 1, [])
            return
        y, m = self.ctx.month
        first, last = month_bounds((y, m))
        names = {e.uid: e.name for e in self.employees()}
        per: dict[int, dict[int, str]] = {}
        for lv in self.ctx.view().list_leave():
            if lv.to_date < first or lv.from_date > last:
                continue
            d = max(lv.from_date, first)
            while d <= min(lv.to_date, last):
                per.setdefault(lv.emp_uid, {})[d.day] = lv.kind or "Leave"
                d += timedelta(days=1)
        rows = sorted(((names.get(u, "?"), days) for u, days in per.items()), key=lambda x: x[0])
        s = self.ctx.view().settings()
        self.strip.set_data(y, m, rows, [x.strip().upper() for x in s.weekend_days.split(",") if x.strip()])
        self.strip_area.setFixedHeight(min(190, max(80, self.strip.height() + 4)))


def set_state_level(w: QWidget, level: str) -> bool:
    """Do not let a warning replace an error that is already shown on the same widget."""
    return not (level == "warn" and w.property("state") == "error")


# ----------------------------------------------------------------------------- fixed shifts
class FixedRow(RowBase):
    def __init__(self, page: "FixedShiftsPage", rec) -> None:
        super().__init__(rec.uid)
        first, last = month_bounds(page.ctx.month)
        self.emp = page.emp_combo(rec.emp_uid)
        self.day = page.date_edit(rec.day, first, last)
        self.shift = QComboBox()
        self.shift.setMinimumWidth(210)
        no_wheel(self.shift)
        self.rec = QComboBox()
        self.rec.setMinimumWidth(150)
        no_wheel(self.rec)
        self.emp.setToolTip(tr("Who works this shift."))
        self.day.setToolTip(tr("The date of the shift. Only days of this month can be chosen."))
        self.shift.setToolTip(tr("The shift. The list shows this person's allowed shifts (all shifts when none are ticked)."))
        self.rec.setToolTip(tr("Where the person works that day. 'Home' means their own reception."))
        page.fill_shift_combo(self, rec.shift_uid)
        page.fill_rec_combo(self, rec.rec_uid)

    def widgets(self):
        return [self.emp, self.day, self.shift, self.rec]

    def field_widgets(self, field):
        return {"emp_uid": [self.emp], "day": [self.day], "shift_uid": [self.shift], "rec_uid": [self.rec]}.get(field, [])


class FixedShiftsPage(MonthTablePage):
    key = "fixed_shifts"
    kind = "fixed"
    title = "Fixed shifts"
    subtitle = ("Shifts that must be placed exactly as entered, for example a training day or an agreed swap. "
                "A fixed shift on a day off is worked as overtime.")
    watches = frozenset({"fixed", "employee", "shift", "reception", "leave"})
    COLS = ["Employee", "Date", "Shift", "Reception", "Check"]
    add_label = "Add fixed shift"
    empty_text = "No fixed shifts in this month. Click 'Add fixed shift' to pin a person to a shift on a date."

    def configure_columns(self, hh) -> None:
        for c, w in ((0, 220), (1, 206), (2, 240), (3, 170)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

    def records(self):
        return self.ctx.view().list_fixed()

    # -- combos
    def allowed_shifts(self, emp_uid):
        view = self.ctx.view()
        shifts = view.list_shifts()
        try:
            allowed = view.get("employee", emp_uid).allowed
        except StoreError:
            allowed = []
        return [s for s in shifts if s.uid in allowed] if allowed else shifts

    def fill_shift_combo(self, r: FixedRow, current) -> None:
        c = r.shift
        c.blockSignals(True)
        c.clear()
        pool = self.allowed_shifts(r.emp.currentData())
        for s in pool:
            c.addItem(disp_code(s.code), s.uid)
        if current is not None and c.findData(current) < 0:
            code = disp_code(next((s.code for s in self.ctx.view().list_shifts() if s.uid == current), "?"))
            c.addItem(code + tr("  (not allowed)"), current)
        c.setCurrentIndex(max(c.findData(current), 0))
        c.blockSignals(False)

    def fill_rec_combo(self, r: FixedRow, current) -> None:
        c = r.rec
        c.blockSignals(True)
        c.clear()
        c.addItem(tr("Home reception"), None)
        for rc in self.ctx.view().list_receptions():
            c.addItem(rc.name, rc.uid)
        c.setCurrentIndex(max(c.findData(current), 0))
        c.blockSignals(False)

    def make_row(self, rec) -> FixedRow:
        r = FixedRow(self, rec)
        r.emp.currentIndexChanged.connect(lambda _i, row=r: self.emp_changed(row))
        r.day.dateChanged.connect(lambda _d, row=r: self.commit(row))
        r.shift.currentIndexChanged.connect(lambda _i, row=r: self.commit(row))
        r.rec.currentIndexChanged.connect(lambda _i, row=r: self.commit(row))
        return r

    def place(self, i: int, row: FixedRow) -> None:
        for c, w in enumerate((row.emp, row.day, row.shift, row.rec)):
            self.table.setCellWidget(i, c, host(w))
        self.table.setCellWidget(i, 4, host(row.msg, stretch=False))

    def emp_changed(self, r: FixedRow) -> None:
        if self._loading:
            return
        pool = self.allowed_shifts(r.emp.currentData())
        cur = r.shift.currentData()
        self.fill_shift_combo(r, cur if any(s.uid == cur for s in pool) else (pool[0].uid if pool else cur))
        self.commit(r)

    def values(self, r: FixedRow) -> dict:
        return {"emp_uid": r.emp.currentData(), "day": pydate(r.day.date()), "shift_uid": r.shift.currentData(),
                "rec_uid": r.rec.currentData()}

    def commit(self, r: FixedRow) -> None:
        if self._loading or r.shift.currentData() is None:
            return
        self.write(r.uid, **self.values(r))

    def new_fields(self):
        emps = [e for e in self.employees() if e.active] or self.employees()
        if not emps:
            self.show_message(tr("Add an employee first (Employees page)."))
            return None
        if not self.ctx.view().list_shifts():
            self.show_message(tr("Add a shift first (Shifts page)."))
            return None
        emp = emps[0]
        sel = self.selected_uid()
        if sel in self.rows:  # same person as the selected row
            emp = next((e for e in emps if e.uid == self.rows[sel].emp.currentData()), emp)
        first, last = month_bounds(self.ctx.month)
        used = {f.day for f in self.ctx.view().list_fixed() if f.emp_uid == emp.uid}
        away = [(lv.from_date, lv.to_date) for lv in self.ctx.view().list_leave() if lv.emp_uid == emp.uid]
        offs = {emp.fixed_off, emp.fixed_off2} - {""}
        d, day = first, None
        while d <= last:
            if d not in used and weekday_name(d) not in offs and not any(a <= d <= b for a, b in away):
                day = d
                break
            d += timedelta(days=1)
        shifts = self.allowed_shifts(emp.uid)
        return {"emp_uid": emp.uid, "day": day or first, "shift_uid": shifts[0].uid, "rec_uid": None}

    def problems(self) -> dict[int, list[tuple[str, str, str]]]:
        view = self.ctx.view()
        emps = {e.uid: e for e in view.list_employees()}
        leave = view.list_leave()
        out: dict[int, list] = {}
        vals = {uid: self.values(r) for uid, r in self.rows.items()}
        for uid, v in vals.items():
            probs = out.setdefault(uid, [])
            e = emps.get(v["emp_uid"])
            if e is None:
                continue
            for lv in leave:
                if lv.emp_uid == e.uid and lv.from_date <= v["day"] <= lv.to_date:
                    probs.append(("day", "error", tr("On leave that day ({v0} - {v1}).", v0=fmt(lv.from_date), v1=fmt(lv.to_date))))
                    break
            if weekday_name(v["day"]) in {e.fixed_off, e.fixed_off2} - {""}:
                probs.append(("day", "warn", tr("Fixed day off: worked as overtime.")))
            if e.allowed and v["shift_uid"] not in e.allowed:
                probs.append(("shift_uid", "warn", tr("Not one of {name}'s allowed shifts.", name=e.name)))
            for u2, w in vals.items():
                if u2 < uid and w["emp_uid"] == v["emp_uid"] and w["day"] == v["day"]:
                    probs.append(("day", "error", tr("Already has a fixed shift that day.")))
                    break
        return out

    def validate_rows(self) -> None:
        probs = self.problems()
        for uid, r in self.rows.items():
            for w in r.widgets():
                set_state(w, None)
            ps = probs.get(uid, [])
            worst = "error" if any(p[1] == "error" for p in ps) else "warn" if any(p[1] == "warn" for p in ps) else "info"
            for field, level, msg in ps:
                for w in r.field_widgets(field):
                    if level != "info" and set_state_level(w, level):
                        set_state(w, msg, level)
            self.set_msg(r, " ".join(dict.fromkeys(p[2] for p in ps)), worst)


# ----------------------------------------------------------------------------- demand
GENDERS = [("Any", "ANY"), ("Men", "M"), ("Women", "F")]
FILLS = [("Home", "HOME"), ("Home + help", "HOME+HELP"), ("Help", "HELP")]


class DemandRow(RowBase):
    def __init__(self, page: "DemandPage", rec) -> None:
        super().__init__(rec.uid)
        view = page.ctx.view()
        self.active = QCheckBox("")
        self.active.setChecked(bool(rec.active))
        self.active.setToolTip(tr("Untick to ignore this row without deleting it."))
        self.rec = QComboBox()
        for r in view.list_receptions():
            self.rec.addItem(r.name, r.uid)
        self.rec.setCurrentIndex(max(self.rec.findData(rec.rec_uid), 0))
        self.shift = QComboBox()
        for s in view.list_shifts():
            self.shift.addItem(disp_code(s.code), s.uid)
        self.shift.setCurrentIndex(max(self.shift.findData(rec.shift_uid), 0))
        self.gender = QComboBox()
        for t, d in [(tr(a), b) for a, b in GENDERS]:
            self.gender.addItem(t, d)
        self.gender.setCurrentIndex(max(self.gender.findData((rec.gender or "ANY").upper()), 0))
        self.fill = QComboBox()
        for t, d in [(tr(a), b) for a, b in FILLS]:
            self.fill.addItem(t, d)
        self.fill.setCurrentIndex(max(self.fill.findData((rec.fill or "HOME").upper()), 0))
        self.prio = QSpinBox()
        self.prio.setRange(1, 99)
        self.prio.setValue(int(min(max(rec.prio, 1), 99)))
        self.prio.setKeyboardTracking(False)
        self.prio.setStyleSheet("QSpinBox { padding: 3px 16px 3px 6px; } QSpinBox::up-button, QSpinBox::down-button { width: 14px; }")
        self.counts: list[QSpinBox] = []
        for i, d in enumerate(WEEKDAYS):
            sp = QSpinBox()
            sp.setRange(-1, 30)
            sp.setSpecialValueText(tr("ALL"))
            sp.setKeyboardTracking(False)
            sp.setValue(int(rec.counts[i]) if i < len(rec.counts) else 0)
            sp.setStyleSheet("QSpinBox { padding: 3px 16px 3px 6px; } QSpinBox::up-button, QSpinBox::down-button { width: 14px; }")
            sp.setToolTip(tr('How many people on {d}. ALL = everybody of this reception and gender who is available.', d=d))
            no_wheel(sp)
            self.counts.append(sp)
        for w in (self.rec, self.shift, self.gender, self.fill, self.prio):
            no_wheel(w)
        self.rec.setToolTip(tr("The reception that needs these people."))
        self.shift.setToolTip(tr("The shift they work."))
        self.gender.setToolTip(tr("Any = men or women; Men / Women = only that gender counts."))
        self.fill.setToolTip(tr("Home = staff of this reception; Home + help = staff of this reception, then helpers from other "
                             "receptions; Help = helpers only."))
        self.prio.setToolTip(tr("Lower numbers are filled first when there are not enough people."))

    def widgets(self):
        return [self.active, self.rec, self.shift, self.gender, self.fill, self.prio, *self.counts]

    def field_widgets(self, field):
        return {"active": [self.active], "rec_uid": [self.rec], "shift_uid": [self.shift], "gender": [self.gender],
                "fill": [self.fill], "prio": [self.prio], "counts": self.counts}.get(field, [])


class DemandPage(MonthTablePage):
    key = "demand"
    kind = "demand"
    title = "Demand (advanced, optional)"
    subtitle = ("Most months do not need this: the roster is built from your Coverage rows alone. Add a demand row only when you want an "
                "exact number of people on one shift, for example two on the evening shift every Sunday. ALL means every "
                "available person of that reception.")
    watches = frozenset({"demand", "shift", "reception"})
    COLS = ["On", "Reception", "Shift", "Who", "Fill from", "Priority", *WEEKDAYS]
    add_label = "Add demand row"
    ROW_H = 44
    empty_text = "No demand rows. That is fine: the roster is built from the Coverage rows."

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.layout().setContentsMargins(12, 16, 12, 16)  # the table needs the width

    def configure_columns(self, hh) -> None:
        widths = [46, 104, 226, 94, 136, 62] + [64] * 7
        for c, w in enumerate(widths):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        hh.setStretchLastSection(False)

    def records(self):
        return self.ctx.view().list_demand()

    def make_row(self, rec) -> DemandRow:
        r = DemandRow(self, rec)
        r.active.toggled.connect(lambda _v, row=r: self.commit(row))
        for c in (r.rec, r.shift, r.gender, r.fill):
            c.currentIndexChanged.connect(lambda _i, row=r: self.commit(row))
        r.prio.valueChanged.connect(lambda _v, row=r: self.commit(row))
        for sp in r.counts:
            sp.valueChanged.connect(lambda _v, row=r: self.commit(row))
        return r

    def place(self, i: int, row: DemandRow) -> None:
        hw = host(row.active, margins=(0, 4, 0, 4), stretch=False)
        hw.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(i, 0, hw)
        for c, w in enumerate((row.rec, row.shift, row.gender, row.fill, row.prio), start=1):
            self.table.setCellWidget(i, c, host(w, margins=(4, 4, 4, 4), stretch=False))
        for j, sp in enumerate(row.counts):
            self.table.setCellWidget(i, 6 + j, host(sp, margins=(2, 4, 2, 4), stretch=False))

    def values(self, r: DemandRow) -> dict:
        return {"active": r.active.isChecked(), "rec_uid": r.rec.currentData(), "shift_uid": r.shift.currentData(),
                "gender": r.gender.currentData(), "fill": r.fill.currentData(), "prio": float(r.prio.value()),
                "counts": [int(sp.value()) for sp in r.counts]}

    def commit(self, r: DemandRow) -> None:
        if self._loading or r.rec.currentData() is None or r.shift.currentData() is None:
            return
        self.write(r.uid, **self.values(r))

    def new_fields(self):
        view = self.ctx.view()
        recs, shifts = view.list_receptions(), view.list_shifts()
        if not recs or not shifts:
            self.show_message(tr("Add a reception and a shift first."))
            return None
        return {"rec_uid": recs[0].uid, "shift_uid": shifts[0].uid, "gender": "ANY", "fill": "HOME", "prio": 99.0,
                "counts": [1, 1, 1, 1, 1, 1, 1], "active": True}

    def validate_rows(self) -> None:
        for r in self.rows.values():
            v = self.values(r)
            empty = v["active"] and not any(c != 0 for c in v["counts"])
            set_state(r.active, tr("This row is on but has no numbers, so it adds nobody. Enter counts or switch it off.") if empty else None,
                      "warn")
            for sp in r.counts:
                set_state(sp, None)

    def apply_read_only(self, ro: bool) -> None:
        super().apply_read_only(ro)


# ----------------------------------------------------------------------------- overview
class MonthPage(Page):
    key = "month"
    title = "Month"
    subtitle = "The month you are planning: what it holds and what you can do with it."
    watches = frozenset({"leave", "fixed", "demand", "employee", "settings"})

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.body.addLayout(row, 1)
        left = QVBoxLayout()
        left.setSpacing(12)
        row.addLayout(left, 3)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 20)
        cl.setSpacing(10)
        top = QHBoxLayout()
        self.name = QLabel("")
        self.name.setProperty("role", "big")
        self.chip = QLabel("")
        self.chip.setObjectName("statusChip")
        top.addWidget(self.name)
        top.addSpacing(12)
        top.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        cl.addLayout(top)
        self.status_text = QLabel("")
        self.status_text.setProperty("role", "subtitle")
        self.status_text.setWordWrap(True)
        cl.addWidget(self.status_text)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(4)
        self.count_labels: dict[str, QLabel] = {}
        for i, (k, text, page) in enumerate((("employees", "Employees working", "employees"), ("leave", "Leave periods", "leave"),
                                              ("fixed", "Fixed shifts", "fixed_shifts"), ("demand", "Demand rows", "demand"))):
            n = QLabel("0")
            n.setProperty("role", "count")
            t = QPushButton(tr(text) + (" ‹" if i18n.is_rtl() else " ›"))
            t.setProperty("flat-link", True)
            t.setCursor(Qt.CursorShape.PointingHandCursor)
            t.setToolTip(tr('Open the {v0} page', v0=tr(text) if i18n.is_ar() else text.lower().split(' ')[0]))
            t.clicked.connect(lambda _c=False, p=page: ctx.request_jump.emit(_PageTarget(p), ""))
            grid.addWidget(n, 0, i, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(t, 1, i, Qt.AlignmentFlag.AlignLeft)
            self.count_labels[k] = n
        cl.addLayout(grid)
        left.addWidget(card)

        btns = QHBoxLayout()
        self.new_btn = QPushButton(tr("New month…"))
        self.new_btn.setProperty("primary", True)
        self.new_btn.setToolTip(tr("Start planning another month."))
        self.new_btn.clicked.connect(ctx.request_new_month.emit)
        self.reopen_btn = QPushButton(tr("Re-open month"))
        self.reopen_btn.setToolTip(tr("Unlock a built month so its leave, fixed shifts and demand can be edited again."))
        self.reopen_btn.clicked.connect(ctx.request_reopen_month.emit)
        self.delete_btn = QPushButton(tr("Delete month"))
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(ctx.request_delete_month.emit)
        for b in (self.new_btn, self.reopen_btn, self.delete_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        left.addLayout(btns)
        self.note = QLabel(tr("A new month starts from your current staff, shifts, coverage and settings. Leave that overlaps it is "
                           "carried over; the fixed shifts can be copied too. Delete removes only this month's leave, fixed shifts, "
                           "demand and roster - never your staff or settings."))
        self.note.setProperty("role", "hint")
        self.note.setWordWrap(True)
        left.addWidget(self.note)
        left.addStretch(1)

        right = QVBoxLayout()
        row.addLayout(right, 2)
        cap = QLabel(tr("All months"))
        cap.setStyleSheet("font-weight: 600;")
        right.addWidget(cap)
        self.months = QListWidget()
        self.months.setMaximumWidth(340)
        self.months.itemClicked.connect(self._pick)
        right.addWidget(self.months, 1)

    def reload(self) -> None:
        ctx = self.ctx
        mi = ctx.month_info
        view = ctx.view()
        self.months.clear()
        for m in ctx.store.list_months():
            it = QListWidgetItem(f"{month_title(m.key)}   ·   {tr('Built') if m.status != 'draft' else tr('Draft')}")
            it.setData(Qt.ItemDataRole.UserRole, m.key)
            self.months.addItem(it)
            if mi and m.key == mi.key:
                self.months.setCurrentItem(it)
        if not mi:
            self.name.setText(tr("No month yet"))
            self.chip.hide()
            self.status_text.setText(tr("Click 'New month…' to start planning."))
            for n in self.count_labels.values():
                n.setText("–")
            return
        built = mi.status != "draft"
        self.name.setText(month_title(mi.key))
        self.chip.setText(tr("Built") if built else tr("Draft"))
        self.chip.setProperty("built", built)
        self.chip.style().unpolish(self.chip)
        self.chip.style().polish(self.chip)
        self.chip.show()
        self.status_text.setText(tr("This month is built and locked. Re-open it to change its inputs.") if built
                                 else tr("Draft: you can still change everything. Build it when the Checks page is happy."))
        self.count_labels["employees"].setText(str(sum(1 for e in view.list_employees() if e.active)))
        self.count_labels["leave"].setText(str(len(view.list_leave())))
        self.count_labels["fixed"].setText(str(len(view.list_fixed())))
        self.count_labels["demand"].setText(str(len(view.list_demand())))

    def _pick(self, it) -> None:
        key = it.data(Qt.ItemDataRole.UserRole)
        if key and key != self.ctx.month:
            self.ctx.set_month(key)

    def apply_read_only(self, ro: bool) -> None:
        mi = self.ctx.month_info
        self.reopen_btn.setVisible(bool(mi) and ro)
        self.delete_btn.setEnabled(bool(mi) and not ro)
        self.delete_btn.setToolTip(tr("Re-open the month first: a built month is protected.") if ro else
                                   tr("Delete this month's leave, fixed shifts, demand and roster."))


class _PageTarget:
    """Pseudo locator that only names a page (used by the links on the overview)."""

    def __init__(self, screen: str) -> None:
        self.screen, self.record_id, self.field, self.label = screen, None, "", screen


__all__ = ["NewMonthDialog", "MonthPage", "LeavePage", "FixedShiftsPage", "DemandPage", "LeaveStrip"]
