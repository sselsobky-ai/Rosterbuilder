"""Coverage screen: how many people each reception needs, hour by hour."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox, QTableWidget,
                               QVBoxLayout, QWidget)

from ..i18n import tr, tr_msg
from ..store import StoreError
from .base import Page
from .widgets import WEEKDAYS, HourBar24, TimePicker, WeekdayChips, expand_days, no_wheel, set_state

_ALL, _WEEKDAYS, _CUSTOM = "ALL", "WEEKDAYS", "CUSTOM"


class CoverageRow:
    def __init__(self, uid: int) -> None:
        self.uid = uid
        self.frm = TimePicker(0, 23.5)
        self.to = TimePicker(0.5, 24)
        self.min_staff = QSpinBox()
        self.min_male = QSpinBox()
        self.max_staff = QSpinBox()
        self.days_mode = QComboBox()
        self.chips = WeekdayChips()
        self.msg = QLabel("")
        self.msg.setProperty("role", "error")
        self.msg.hide()
        for sp, hi in ((self.min_staff, 50), (self.min_male, 50), (self.max_staff, 50)):
            sp.setRange(0, hi)
            sp.setKeyboardTracking(False)
            sp.setMinimumWidth(84)
            no_wheel(sp)
        self.min_male.setSpecialValueText(tr("none"))
        self.max_staff.setSpecialValueText(tr("no limit"))
        self.days_mode.addItem(tr("All days"), _ALL)
        self.days_mode.addItem(tr("Weekdays (not Friday)"), _WEEKDAYS)
        self.days_mode.addItem(tr("Pick days…"), _CUSTOM)
        no_wheel(self.days_mode)
        self.frm.setToolTip(tr("First hour that needs this cover."))
        self.to.setToolTip(tr("The hour the cover ends (12 MN = end of the day). Cover cannot run past midnight: add a second row for the next morning."))
        self.min_staff.setToolTip(tr("At least this many people on duty in every hour of the row."))
        self.min_male.setToolTip(tr("At least this many men on duty in every hour of the row ('none' = no rule)."))
        self.max_staff.setToolTip(tr("No more than this many people on duty at once ('no limit' = no rule)."))
        self.days_mode.setToolTip(tr("Which days of the week the row applies to."))
        self.chips.setToolTip(tr("Tick the days this row applies to."))


class CoveragePage(Page):
    key = "coverage"
    title = "Coverage"
    subtitle = ("How many people each reception needs, hour by hour. Every hour of the day should have a row with at least 1 person; "
                "hours without a row show in red below.")
    watches = frozenset({"reception", "target", "settings"})

    COLS = ["From", "To", "Min people", "Min men", "Max people", "Days"]

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Reception:")))
        self.rec_combo = QComboBox()
        self.rec_combo.setMinimumWidth(220)
        self.rec_combo.setToolTip(tr("Choose the reception whose coverage you want to edit."))
        self.rec_combo.currentIndexChanged.connect(self._rec_changed)
        top.addWidget(self.rec_combo)
        top.addSpacing(16)
        self.add_btn = QPushButton(tr("Add row"))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self.add_row)
        self.dup_btn = QPushButton(tr("Duplicate"))
        self.dup_btn.clicked.connect(self.duplicate_row)
        self.del_btn = QPushButton(tr("Delete row"))
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self.delete_row)
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            top.addWidget(b)
        top.addStretch(1)
        self.body.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.table.horizontalHeader()
        for c, w in ((0, 120), (1, 120), (2, 110), (3, 110), (4, 120)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(150)
        self.body.addWidget(self.table, 1)
        cap = QLabel(tr("Hours covered, per weekday"))
        cap.setStyleSheet("font-weight: 600;")
        self.body.addWidget(cap)
        self.bar = HourBar24()
        self.body.addWidget(self.bar)
        self.legend = QLabel(tr("The number is the minimum staff in that hour. Green strip = at least one man needed. "
                             "Red = no coverage row for that hour. Grey = row with no minimum."))
        self.legend.setProperty("role", "hint")
        self.legend.setWordWrap(True)
        self.body.addWidget(self.legend)
        self.rows: dict[int, CoverageRow] = {}
        self._rec: int | None = None

    # -- data
    def current_reception(self) -> int | None:
        return self.rec_combo.currentData()

    def reload(self) -> None:
        keep = self._rec
        self.rec_combo.blockSignals(True)
        self.rec_combo.clear()
        for r in self.ctx.view().list_receptions():
            self.rec_combo.addItem(r.name, r.uid)
        i = self.rec_combo.findData(keep)
        self.rec_combo.setCurrentIndex(i if i >= 0 else 0)
        self.rec_combo.blockSignals(False)
        self._rec = self.current_reception()
        self._rebuild_rows()

    def _rec_changed(self, _i) -> None:
        self._rec = self.current_reception()
        self._loading = True
        try:
            self._rebuild_rows()
        finally:
            self._loading = False
        self.apply_read_only(self.ctx.read_only)

    def _rebuild_rows(self) -> None:
        sel = self._selected_uid()
        self.table.setRowCount(0)
        self.rows.clear()
        for t in self.ctx.view().list_targets():
            if t.rec_uid == self._rec:
                self._append_row(t)
        if sel in self.rows:
            self.table.selectRow(list(self.rows).index(sel))
        self._update_bar()

    def _append_row(self, t) -> CoverageRow:
        r = CoverageRow(t.uid)
        self.rows[t.uid] = r
        i = self.table.rowCount()
        self.table.insertRow(i)
        r.frm.setValue(t.frm_h)
        r.to.setValue(t.to_h)
        r.min_staff.setValue(int(t.min_staff or 0))
        r.min_male.setValue(int(t.min_male or 0))
        r.max_staff.setValue(int(t.max_staff or 0))
        self._set_days(r, t.days)
        for c, w in enumerate((r.frm, r.to, r.min_staff, r.min_male, r.max_staff)):
            host = QWidget()
            h = QHBoxLayout(host)
            h.setContentsMargins(8, 4, 8, 4)
            h.addWidget(w)
            h.addStretch(1)
            self.table.setCellWidget(i, c, host)
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(8, 2, 8, 2)
        v.setSpacing(0)
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(r.days_mode)
        line.addWidget(r.chips)
        line.addStretch(1)
        v.addLayout(line)
        v.addWidget(r.msg)
        self.table.setCellWidget(i, 5, host)
        self._validate(r)
        for w in (r.frm, r.to):
            w.valueChanged.connect(lambda _v, row=r: self._edited(row))
        for w in (r.min_staff, r.min_male, r.max_staff):
            w.valueChanged.connect(lambda _v, row=r: self._edited(row))
        r.days_mode.currentIndexChanged.connect(lambda _i, row=r: self._mode_changed(row))
        r.chips.changed.connect(lambda _d, row=r: self._edited(row))
        return r

    def _set_days(self, r: CoverageRow, days: str) -> None:
        x = (days or "ALL").strip().upper()
        r.days_mode.blockSignals(True)
        r.days_mode.setCurrentIndex(0 if x in ("", "ALL") else 1 if x == "WEEKDAYS" else 2)
        r.days_mode.blockSignals(False)
        r.chips.set_days(expand_days(x, self._fri()))
        r.chips.setVisible(r.days_mode.currentData() == _CUSTOM)

    def _fri(self) -> str:
        return self.ctx.view().settings().fri_name

    def _days_text(self, r: CoverageRow) -> str:
        m = r.days_mode.currentData()
        if m == _ALL:
            return "ALL"
        if m == _WEEKDAYS:
            return "WEEKDAYS"
        return ",".join(r.chips.days()) or "ALL"

    def _mode_changed(self, r: CoverageRow) -> None:
        custom = r.days_mode.currentData() == _CUSTOM
        if custom and not r.chips.days():
            r.chips.set_days(WEEKDAYS)
        r.chips.setVisible(custom)
        self._edited(r)

    # -- editing
    def _validate(self, r: CoverageRow) -> str:
        msg = ""
        lvl = "error"
        kind = ""
        if r.frm.value() >= r.to.value():
            kind = "time"
            msg = tr("'From' must be earlier than 'To'. Cover cannot run past midnight – add a second row for the morning.")
        elif r.max_staff.value() and r.min_staff.value() > r.max_staff.value():
            kind = "max"
            msg = tr("Min people is more than Max people.")
        elif r.min_male.value() > r.min_staff.value():
            kind = "men"
            msg, lvl = tr("Min men is more than Min people."), "warn"
        elif r.chips.isVisible() or r.days_mode.currentData() == _CUSTOM:
            if not r.chips.days():
                msg = tr("Pick at least one day.")
        r.msg.setProperty("role", "warn" if lvl == "warn" else "error")
        r.msg.style().unpolish(r.msg)
        r.msg.style().polish(r.msg)
        r.msg.setText(msg)
        r.msg.setVisible(bool(msg))
        bad_time = kind == "time"
        bad_max = kind == "max"
        set_state(r.frm, msg if bad_time else None)
        set_state(r.to, msg if bad_time else None)
        set_state(r.min_staff, msg if bad_max else None)
        set_state(r.max_staff, msg if bad_max else None)
        set_state(r.min_male, msg if kind == "men" else None, "warn")
        return msg if lvl == "error" else ""

    def _edited(self, r: CoverageRow) -> None:
        if self._loading:
            return
        self._validate(r)
        mm, mx = r.min_male.value(), r.max_staff.value()
        try:
            self.ctx.update("target", r.uid, origin=self, frm_h=r.frm.value(), to_h=r.to.value(), min_staff=float(r.min_staff.value()),
                            min_male=float(mm) if mm else None, max_staff=float(mx) if mx else None, days=self._days_text(r))
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
        self._update_bar()

    def _update_bar(self) -> None:
        rows = [(r.frm.value(), r.to.value(), r.min_staff.value(), r.min_male.value(), self._days_text(r)) for r in self.rows.values()]
        self.bar.set_rows(rows, self._fri())

    def focus_record(self, record_id, field: str, message: str = "") -> None:
        if record_id is None:
            return
        t = self.ctx.view().get("target", record_id)
        if t.rec_uid != self._rec:
            self.rec_combo.setCurrentIndex(max(self.rec_combo.findData(t.rec_uid), 0))
        if record_id in self.rows:
            row = list(self.rows).index(record_id)
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))
            r = self.rows[record_id]
            w = {"frm_h": r.frm, "to_h": r.to, "min_staff": r.min_staff, "min_male": r.min_male, "max_staff": r.max_staff,
                 "days": r.days_mode, "rec_uid": self.rec_combo}.get(field)
            if w is not None:
                set_state(w, message or "Please check this field.")

    def _selected_uid(self) -> int | None:
        i = self.table.currentRow()
        keys = list(self.rows)
        return keys[i] if 0 <= i < len(keys) else None

    def add_row(self) -> None:
        if self._rec is None:
            self.show_message(tr("Add a reception first."))
            return
        last = list(self.rows.values())[-1] if self.rows else None
        frm = last.to.value() if last and last.to.value() < 24 else 8.0
        to = min(frm + 8, 24.0) if frm < 24 else 24.0
        try:
            t = self.ctx.add("target", origin=self, rec_uid=self._rec, frm_h=frm, to_h=to, min_staff=1.0)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._append_new(t)

    def duplicate_row(self) -> None:
        uid = self._selected_uid()
        if uid is None:
            self.show_message(tr("Select a row first."))
            return
        s = self.ctx.view().get("target", uid)
        try:
            t = self.ctx.add("target", origin=self, rec_uid=s.rec_uid, frm_h=s.frm_h, to_h=s.to_h, min_staff=s.min_staff,
                             min_male=s.min_male, max_staff=s.max_staff, days=s.days)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._append_new(t)

    def _append_new(self, t) -> None:
        self._loading = True
        try:
            self._append_row(t)
        finally:
            self._loading = False
        self.table.selectRow(self.table.rowCount() - 1)
        self.table.scrollToBottom()
        self._update_bar()
        self.show_message("")

    def delete_row(self) -> None:
        uid = self._selected_uid()
        if uid is None:
            self.show_message(tr("Select a row first."))
            return
        try:
            self.ctx.delete("target", uid, origin=self)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._rebuild_rows()
        self.show_message("")

    def apply_read_only(self, ro: bool) -> None:
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            b.setEnabled(not ro)
        for r in self.rows.values():
            for w in (r.frm, r.to, r.min_staff, r.min_male, r.max_staff, r.days_mode):
                w.setEnabled(not ro)
            r.chips.set_read_only(ro)
