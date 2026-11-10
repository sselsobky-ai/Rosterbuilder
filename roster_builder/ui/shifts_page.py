"""Shifts screen: shifts defined by their times; the code (name) is generated."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QWidget)

from ..i18n import disp_code, tr, tr_msg
from ..model import shift_code_from_times
from ..store import DuplicateError, InUseError, StoreError
from .base import Page
from .receptions_page import in_use_message
from .widgets import TimePicker, no_wheel, set_state

_CANDIDATES = [(6, 14), (7, 15), (8, 16), (9, 17), (10, 18), (12, 20), (14, 22), (22, 6), (16, 24), (0, 8), (11, 19), (13, 21)]


def to_stored_end(start: float, end: float) -> float:
    """End time of day -> stored end (an end at or before the start means the next day: +24 h)."""
    return end + 24.0 if end <= start else end


def from_stored_end(end: float) -> float:
    """Stored end -> time of day (30 -> 06 AM, 24 -> 12 MN)."""
    return end % 24.0


def span_hours(start: float, end: float, start2=None, end2=None) -> float:
    h = end - start
    if start2 is not None and end2 is not None:
        h += end2 - start2
    return h


class ShiftRow:
    """The widgets of one table row."""

    def __init__(self, uid: int) -> None:
        self.uid = uid
        self.code = QLabel("")
        self.start = TimePicker()
        self.end = TimePicker()
        self.split = QCheckBox(tr("Split"))
        self.start2 = TimePicker()
        self.end2 = TimePicker()
        self.paid = QDoubleSpinBox()
        self.total = QLabel("")
        self.night = QLabel("")
        self.msg = QLabel("")
        self.paid.setRange(0, 24)
        self.paid.setDecimals(1)
        self.paid.setSingleStep(0.5)
        self.paid.setSuffix(tr(" h"))
        self.paid.setKeyboardTracking(False)
        no_wheel(self.paid)
        self.start.setToolTip(tr("When the shift starts."))
        self.end.setToolTip(tr("When the shift ends. An end at or before the start means the shift ends the next day."))
        self.split.setToolTip(tr("Tick for a split shift with a second part later in the day (for example 08 AM-12 PM and 04 PM-08 PM)."))
        self.start2.setToolTip(tr("When the second part starts."))
        self.end2.setToolTip(tr("When the second part ends."))
        self.paid.setToolTip(tr("Hours that count towards the weekly limit (a break can make this less than the total)."))
        self.total.setToolTip(tr("Hours between start and end (both parts together)."))
        self.night.setToolTip(tr("Overnight: the shift ends the next day. Its late hours count on that day."))
        for w in (self.start2, self.end2):
            w.setVisible(False)
        self.msg.setProperty("role", "error")
        self.msg.hide()

    def second(self) -> bool:
        return self.split.isChecked()


class ShiftsPage(Page):
    key = "shifts"
    title = "Shifts"
    subtitle = "Define each shift by its times. The name is made from the times, so it can never disagree with them."
    watches = frozenset({"shift"})

    COLS = ["Name (made from times)", "First part", "Second part (split shift)", "Paid hours", "Total hours", "Overnight"]

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        bar = QHBoxLayout()
        self.add_btn = QPushButton(tr("Add shift"))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self.add_shift)
        self.dup_btn = QPushButton(tr("Duplicate"))
        self.dup_btn.clicked.connect(self.duplicate_shift)
        self.del_btn = QPushButton(tr("Delete"))
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self.delete_shift)
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        self.body.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 256), (2, 332), (3, 104), (4, 96), (5, 94)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        self.body.addWidget(self.table, 1)
        self.rows: dict[int, ShiftRow] = {}

    # -- build
    def reload(self) -> None:
        sel = self._selected_uid()
        self.table.setRowCount(0)
        self.rows.clear()
        for s in self.ctx.view().list_shifts():
            self._append_row(s)
        if sel in self.rows:
            self.table.selectRow(list(self.rows).index(sel))

    def _append_row(self, s) -> ShiftRow:
        r = ShiftRow(s.uid)
        self.rows[s.uid] = r
        i = self.table.rowCount()
        self.table.insertRow(i)
        second = s.start2 is not None and s.end2 is not None
        r.start.setValue(s.start1)
        r.end.setValue(from_stored_end(s.end1))
        r.split.setChecked(second)
        if second:
            r.start2.setValue(s.start2)
            r.end2.setValue(from_stored_end(s.end2))
        r.paid.setValue(s.paid)
        self._fill_labels(r, s.code)
        r.start2.setVisible(second)
        r.end2.setVisible(second)
        self._place(i, r)
        for w in (r.start, r.end, r.start2, r.end2):
            w.valueChanged.connect(lambda _v, row=r: self._times_edited(row))
        r.split.toggled.connect(lambda on, row=r: self._split_toggled(row, on))
        r.paid.valueChanged.connect(lambda _v, row=r: self._commit(row))
        return r

    def _place(self, i: int, r: ShiftRow) -> None:
        def box(*ws) -> QWidget:
            host = QWidget()
            h = QHBoxLayout(host)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            for w in ws:
                h.addWidget(w)
            h.addStretch(1)
            return host

        code_host = QWidget()
        from PySide6.QtWidgets import QVBoxLayout

        v = QVBoxLayout(code_host)
        v.setContentsMargins(8, 2, 8, 2)
        v.setSpacing(0)
        v.addWidget(r.code)
        v.addWidget(r.msg)
        to1 = QLabel(tr("to"))
        to1.setProperty("role", "unit")
        to2 = QLabel(tr("to"))
        to2.setProperty("role", "unit")
        self.table.setCellWidget(i, 0, code_host)
        self.table.setCellWidget(i, 1, box(r.start, to1, r.end))
        self.table.setCellWidget(i, 2, box(r.split, r.start2, to2, r.end2))
        r._to2 = to2  # type: ignore[attr-defined]
        to2.setVisible(r.split.isChecked())
        self.table.setCellWidget(i, 3, box(r.paid))
        self.table.setCellWidget(i, 4, box(r.total))
        self.table.setCellWidget(i, 5, box(r.night))

    def _values(self, r: ShiftRow):
        s1 = r.start.value()
        e1 = to_stored_end(s1, r.end.value())
        if r.second():
            s2 = r.start2.value()
            e2 = to_stored_end(s2, r.end2.value())
            return s1, e1, s2, e2
        return s1, e1, None, None

    def _fill_labels(self, r: ShiftRow, code: str | None = None) -> None:
        s1, e1, s2, e2 = self._values(r)
        r.code.setText(disp_code(code or shift_code_from_times(s1, e1, s2, e2)))
        f = r.code.font()
        f.setBold(True)
        r.code.setFont(f)
        tot = span_hours(s1, e1, s2, e2)
        r.total.setText(tr("{tot:g} h", tot=tot))
        r.night.setText(tr("Overnight") if max(e1, e2 or 0) > 24 else "")

    def focus_record(self, record_id, field: str, message: str = "") -> None:
        keys = list(self.rows)
        if record_id in self.rows:
            row = keys.index(record_id)
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))
            r = self.rows[record_id]
            w = {"start1": r.start, "end1": r.end, "start2": r.start2, "end2": r.end2, "paid": r.paid}.get(field)
            if w is not None:
                set_state(w, message or "Please check this field.")

    def _selected_uid(self) -> int | None:
        i = self.table.currentRow()
        keys = list(self.rows)
        return keys[i] if 0 <= i < len(keys) else None

    # -- editing
    def _split_toggled(self, r: ShiftRow, on: bool) -> None:
        for w in (r.start2, r.end2, r._to2):  # type: ignore[attr-defined]
            w.setVisible(on)
        if on and not self._loading:
            # a sensible default second part: starts two hours after the first part ends
            e1 = from_stored_end(self._values(r)[1])
            r.start2.blockSignals(True)
            r.end2.blockSignals(True)
            s2 = e1 + 4 if e1 + 4 <= 19.5 else 16.0
            r.start2.setValue(s2)
            r.end2.setValue(min(s2 + 4, 23.5))
            r.start2.blockSignals(False)
            r.end2.blockSignals(False)
        self._times_edited(r)

    def _times_edited(self, r: ShiftRow) -> None:
        if self._loading:
            return
        old_total = float(r.total.text().split()[0]) if r.total.text() else None
        follow = old_total is not None and abs(r.paid.value() - old_total) < 1e-9
        s1, e1, s2, e2 = self._values(r)
        if follow:
            r.paid.blockSignals(True)
            r.paid.setValue(min(24.0, max(0.0, span_hours(s1, e1, s2, e2))))
            r.paid.blockSignals(False)
        self._commit(r)

    def _commit(self, r: ShiftRow) -> None:
        if self._loading:
            return
        s1, e1, s2, e2 = self._values(r)
        self._fill_labels(r)
        problem = ""
        if e1 - s1 >= 24 or (s2 is not None and e2 - s2 >= 24):
            problem = tr("The shift ends when it starts.")
        elif s2 is not None and e1 <= 24 and s2 < e1:
            problem = tr("The second part must start after the first part ends.")
        elif r.paid.value() > span_hours(s1, e1, s2, e2) + 1e-9:
            problem = tr("Paid hours are more than the shift length.")
        for w in (r.start, r.end, r.start2, r.end2):
            set_state(w, problem or None)
        set_state(r.paid, problem if "Paid" in problem else None)
        r.msg.setText(problem)
        r.msg.setVisible(bool(problem))
        if problem and "Paid" not in problem:
            return
        try:
            rec = self.ctx.update("shift", r.uid, origin=self, start1=s1, end1=e1, start2=s2, end2=e2, paid=r.paid.value())
        except DuplicateError:
            msg = tr("Another shift already has these times ({v0}).", v0=shift_code_from_times(s1, e1, s2, e2))
            r.msg.setText(msg)
            r.msg.show()
            r.code.setStyleSheet("color: #DC2626;")
            for w in (r.start, r.end, r.start2, r.end2):
                set_state(w, msg)
            self.show_message(msg)
            return
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        r.code.setStyleSheet("")
        if not problem:
            r.msg.hide()
        for w in (r.start, r.end, r.start2, r.end2):
            set_state(w, None)
        self.show_message("")
        self._fill_labels(r, rec.code)

    def _free_times(self) -> tuple[float, float]:
        taken = {s.code for s in self.ctx.view().list_shifts()}
        for a, b in _CANDIDATES:
            if shift_code_from_times(a, to_stored_end(a, b % 24)) not in taken:
                return float(a), float(to_stored_end(a, b % 24))
        for a in range(0, 24):
            e = to_stored_end(a, (a + 8) % 24)
            if shift_code_from_times(a, e) not in taken:
                return float(a), float(e)
        return 6.0, 14.0

    def add_shift(self) -> None:
        a, b = self._free_times()
        try:
            rec = self.ctx.add("shift", origin=self, start1=a, end1=b, paid=min(b - a, 24.0))
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._loading = True
        try:
            r = self._append_row(rec)
        finally:
            self._loading = False
        self.table.selectRow(self.table.rowCount() - 1)
        self.table.scrollToBottom()
        r.start.setFocus()
        self.show_message("")

    def duplicate_shift(self) -> None:
        uid = self._selected_uid()
        if uid is None:
            self.show_message(tr("Select a shift first."))
            return
        src = self.ctx.view().get("shift", uid)
        shift = 0.5
        while shift < 24:
            s1 = (src.start1 + shift) % 24
            e1 = s1 + (src.end1 - src.start1)
            s2 = e2 = None
            if src.start2 is not None:
                s2 = (src.start2 + shift) % 24
                e2 = s2 + (src.end2 - src.start2)
            code = shift_code_from_times(s1, e1, s2, e2)
            if all(code != s.code for s in self.ctx.view().list_shifts()):
                break
            shift += 0.5
        try:
            rec = self.ctx.add("shift", origin=self, start1=s1, end1=e1, start2=s2, end2=e2, paid=src.paid)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._loading = True
        try:
            self._append_row(rec)
        finally:
            self._loading = False
        self.table.selectRow(self.table.rowCount() - 1)
        self.table.scrollToBottom()
        self.show_message(tr("The copy starts 30 minutes later (or more) so that its name is different. Adjust the times as needed."))

    def delete_shift(self) -> None:
        uid = self._selected_uid()
        if uid is None:
            self.show_message(tr("Select a shift first."))
            return
        s = self.ctx.view().get("shift", uid)
        users = [e.name for e in self.ctx.view().list_employees() if uid in e.allowed or uid in e.pref]
        if users and not self.ctx.confirm(tr("'{code}' is allowed for {v1} employee(s). Delete it and remove it from them?", code=s.code, v1=len(users))):
            return
        try:
            self.ctx.delete("shift", uid, origin=self)
        except InUseError as exc:
            self.show_message(in_use_message("shift", s.code, exc, tr("Remove those fixed shifts / demand rows first.")))
            return
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self.reload()
        self.show_message("")

    def apply_read_only(self, ro: bool) -> None:
        self.add_btn.setEnabled(not ro)
        self.dup_btn.setEnabled(not ro)
        self.del_btn.setEnabled(not ro)
        for r in self.rows.values():
            for w in (r.start, r.end, r.start2, r.end2, r.split, r.paid):
                w.setEnabled(not ro)
