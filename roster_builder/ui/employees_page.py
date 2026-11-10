"""Employees screen: searchable list on the left, the selected person's form on the right."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..i18n import fold, disp_code, tr, tr_msg
from ..store import DuplicateError, StoreError
from .base import Page
from .widgets import WeekdayChips, no_wheel, set_state, set_tip


_GENDER_ICONS: dict = {}


def _gender_icon(gender: str):
    """Small coloured dot: blue = male, magenta = female."""
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    if gender not in _GENDER_ICONS:
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2563EB" if gender == "M" else "#C026D3" if gender == "F" else "#9CA3AF"))
        p.drawEllipse(2, 2, 10, 10)
        p.end()
        _GENDER_ICONS[gender] = QIcon(pm)
    return _GENDER_ICONS[gender]


OT_RANK_HINT = ("1 = first to get overtime (full duty, partial cover or extended hours). Ranked staff work their days off "
                "as overtime before anyone ranked lower. Limits from Settings still apply.")


SEARCH_ROLE = Qt.ItemDataRole.UserRole + 2


def _search_blob(e, recs) -> str:
    """Hidden search text of a list item: gender words in both languages (so "Female" / "أنثى" find the person either way)."""
    words = {"M": "male man ذكر رجل", "F": "female woman أنثى انثى امرأة"}.get(e.gender, "")
    return f"{words} {'' if e.active else 'inactive غير نشط'}"


def _rank_spin() -> QSpinBox:
    """Spin box 1-10 for an OT priority; 0 shows as 'none' (used by the form)."""
    sp = QSpinBox()
    sp.setRange(0, 10)
    sp.setSpecialValueText(tr("none"))
    sp.setKeyboardTracking(False)
    no_wheel(sp)
    return sp


class OtPriorityPanel(QWidget):
    """The 'Overtime priority' list: ranked employees sorted by rank, add / change rank / remove rank."""

    def __init__(self, page: "EmployeesPage") -> None:
        super().__init__()
        self.page = page
        self.ctx = page.ctx
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.intro = QLabel(tr(OT_RANK_HINT) + tr("\nOvertime goes to rank 1 first, then rank 2 and so on; staff without a rank come last. "
                            "Overtime is still used only when it is needed."))
        self.intro.setProperty("role", "subtitle")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)
        self.note = QLabel("")
        self.note.setProperty("role", "warn")
        self.note.setWordWrap(True)
        self.note.hide()
        lay.addWidget(self.note)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Rank", "Employee", "Home reception", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(3, 150)
        lay.addWidget(self.table, 1)
        self.empty = QLabel(tr("Nobody is ranked yet. Everyone with overtime allowed is treated the same."))
        self.empty.setProperty("role", "hint")
        lay.addWidget(self.empty)

        add = QGroupBox(tr("Add to the list"))
        h = QHBoxLayout(add)
        h.setSpacing(10)
        h.addWidget(QLabel(tr("Employee")))
        self.who = QComboBox()
        self.who.setMinimumWidth(240)
        no_wheel(self.who)
        set_tip(self.who, tr("Staff who may work overtime and have no rank yet."))
        h.addWidget(self.who)
        h.addWidget(QLabel(tr("Rank")))
        self.rank = QSpinBox()
        self.rank.setRange(1, 10)
        no_wheel(self.rank)
        set_tip(self.rank, tr("1 = first to get overtime, 10 = last of the ranked staff."))
        h.addWidget(self.rank)
        self.add_btn = QPushButton(tr("Add"))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self._add)
        h.addWidget(self.add_btn)
        h.addStretch(1)
        lay.addWidget(add)

    # -- data
    def reload(self) -> None:
        view = self.ctx.view()
        s = view.settings()
        recs = {r.uid: r.name for r in view.list_receptions()}
        emps = view.list_employees()
        ranked = sorted((e for e in emps if e.ot and e.ot_rank is not None), key=lambda e: (e.ot_rank, e.name.upper()))
        self._loading = True
        try:
            self.table.setRowCount(len(ranked))
            for i, e in enumerate(ranked):
                sp = QSpinBox()
                sp.setRange(1, 10)
                sp.setKeyboardTracking(False)
                sp.setValue(e.ot_rank)
                no_wheel(sp)
                set_tip(sp, tr('Change the overtime rank of {name}.', name=e.name))
                sp.valueChanged.connect(lambda v, uid=e.uid: self._set_rank(uid, v))
                sp.setProperty("employee_uid", e.uid)
                self.table.setCellWidget(i, 0, sp)
                name = QTableWidgetItem(e.name + ("" if e.active else tr("  (inactive)")))
                if not e.active:
                    name.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(i, 1, name)
                self.table.setItem(i, 2, QTableWidgetItem(recs.get(e.home_uid, "?")))
                b = QPushButton(tr("Remove rank"))
                b.setProperty("danger", True)
                b.setMinimumWidth(120)
                set_tip(b, tr("{name} goes back to 'no rank'.", name=e.name))
                b.clicked.connect(lambda _c=False, uid=e.uid: self._set_rank(uid, None))
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(6, 4, 6, 4)
                hl.addWidget(b)
                self.table.setCellWidget(i, 3, holder)
                self.table.setRowHeight(i, 46)
            self.empty.setVisible(not ranked)
            self.table.setVisible(bool(ranked))
            free = [e for e in emps if e.ot and e.ot_rank is None and e.active]
            self.who.clear()
            for e in free:
                self.who.addItem(e.name, e.uid)
            self.rank.setValue(min(10, max([e.ot_rank for e in ranked] + [1])))
            can = bool(free) and not self.ctx.read_only
            self.who.setEnabled(can)
            self.rank.setEnabled(can)
            self.add_btn.setEnabled(can)
            if not s.ot_on:
                self.note.setText(tr("Overtime is switched off in Settings, so these ranks have no effect until it is switched on."))
            elif not any(e.ot for e in emps):
                self.note.setText(tr("Nobody may work overtime yet. Tick 'Overtime allowed' on the person's form first."))
            elif not free and not ranked:
                self.note.setText("")
            else:
                self.note.setText("")
            self.note.setVisible(bool(self.note.text()))
        finally:
            self._loading = False

    def set_read_only(self, ro: bool) -> None:
        for i in range(self.table.rowCount()):
            for c in (0, 3):
                w = self.table.cellWidget(i, c)
                if w is not None:
                    w.setEnabled(not ro)
        for w in (self.who, self.rank, self.add_btn):
            w.setEnabled(w.isEnabled() and not ro)

    # -- actions
    def _set_rank(self, uid: int, rank: int | None) -> None:
        if self._loading:
            return
        try:
            self.ctx.update("employee", uid, origin=self, ot_rank=rank)
        except StoreError as exc:
            self.page.show_message(tr_msg(str(exc)))
        else:
            self.page.show_message("")
        QTimer.singleShot(0, self.page.refresh)

    def _add(self) -> None:
        uid = self.who.currentData()
        if uid is None:
            return
        self._set_rank(uid, self.rank.value())


class EmployeesPage(Page):
    key = "employees"
    title = "Employees"
    subtitle = "Everyone who can be rostered. Pick a person on the left; every change is saved as you make it."
    watches = frozenset({"employee", "reception", "shift", "settings"})

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._uid: int | None = None
        # two views: the people (list + form) and the overtime priority list
        bar = QHBoxLayout()
        bar.setSpacing(0)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.tab_people = QPushButton(tr("People"))
        self.tab_ot = QPushButton(tr("Overtime priority"))
        set_tip(self.tab_people, tr("The list of employees and the form of the selected person."))
        set_tip(self.tab_ot, tr("Rank who gets overtime first (1 = first)."))
        for i, b in enumerate((self.tab_people, self.tab_ot)):
            b.setCheckable(True)
            b.setProperty("seg", "left" if i == 0 else "right")
            self.view_group.addButton(b, i)
            bar.addWidget(b)
        self.tab_people.setChecked(True)
        bar.addStretch(1)
        self.body.addLayout(bar)
        self.stack = QStackedWidget()
        self.body.addWidget(self.stack, 1)
        people = QWidget()
        row = QHBoxLayout(people)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(16)
        self.stack.addWidget(people)
        self.ot_panel = OtPriorityPanel(self)
        self.stack.addWidget(self.ot_panel)
        self.view_group.idClicked.connect(self._switch_view)

        # ---- left: search + list + buttons
        left = QVBoxLayout()
        left.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search name, reception, gender…"))
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(tr("Type part of a name, a reception, or Male / Female to filter the list."))
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumWidth(270)
        self.list.setMaximumWidth(330)
        self.list.currentItemChanged.connect(self._selected)
        left.addWidget(self.list, 1)
        btns = QHBoxLayout()
        self.add_btn = QPushButton(tr("Add"))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self.add_employee)
        self.dup_btn = QPushButton(tr("Duplicate"))
        self.dup_btn.clicked.connect(self.duplicate_employee)
        self.del_btn = QPushButton(tr("Delete"))
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self.delete_employee)
        for b in (self.add_btn, self.dup_btn, self.del_btn):
            btns.addWidget(b)
        left.addLayout(btns)
        row.addLayout(left)

        # ---- right: form
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("pageBody")
        self.scroll.setWidget(host)
        row.addWidget(self.scroll, 1)
        self.form_host = host
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setProperty("role", "msg")
        self.summary.setWordWrap(True)
        self.summary.hide()
        col.addWidget(self.summary)

        # who
        who = QGroupBox(tr("Who"))
        f = QGridLayout(who)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        f.setColumnStretch(1, 1)
        f.setColumnStretch(3, 1)
        self.name = QLineEdit()
        set_tip(self.name, tr("The name shown on the roster."))
        self.name.setMaxLength(60)
        self.name.textEdited.connect(lambda _t: self._d_name.trigger())
        self.name.editingFinished.connect(self.flush)
        self.gender = QComboBox()
        self.gender.addItem(tr("Male"), "M")
        self.gender.addItem(tr("Female"), "F")
        set_tip(self.gender, tr("Men-only hours and 'at least one man' rules use this."))
        self.home = QComboBox()
        set_tip(self.home, tr("The reception this person normally works at."))
        self.also = QComboBox()
        set_tip(self.also, tr("Optional second reception this person may help at when it is short of staff."))
        self.active = QCheckBox(tr("Active (is rostered)"))
        set_tip(self.active, tr("Untick for someone who is away for good or not rostered this month."))
        for w in (self.gender, self.home, self.also):
            no_wheel(w)
            w.setMinimumWidth(160)
        f.addWidget(QLabel(tr("Name")), 0, 0)
        f.addWidget(self.name, 0, 1, 1, 3)
        f.addWidget(QLabel(tr("Gender")), 1, 0)
        f.addWidget(self.gender, 1, 1)
        f.addWidget(QLabel(tr("Home reception")), 1, 2)
        f.addWidget(self.home, 1, 3)
        f.addWidget(self.active, 2, 0, 1, 2)
        f.addWidget(QLabel(tr("Can also cover")), 2, 2)
        f.addWidget(self.also, 2, 3)
        col.addWidget(who)

        # days
        days = QGroupBox(tr("Days"))
        f = QFormLayout(days)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        self.fixed = WeekdayChips()
        set_tip(self.fixed, tr("Pick this person's fixed days off: one block of neighbouring days (at most the 'days off per week' in "
                            "Settings, and never more than 2). Leave empty to let the app choose."))
        self.fixed.set_consecutive(2)
        self.fixed.changed.connect(self._fixed_changed)
        self.fixed.rejected.connect(lambda m: self.show_message(m))
        self.fixed_hint = QLabel(tr("Leave empty and the app chooses the days off."))
        self.fixed_hint.setProperty("role", "hint")
        self.fri = QCheckBox(tr("Can work Fridays"))
        set_tip(self.fri, tr("Untick if this person never works on the Friday of the week."))
        self.no_we = QCheckBox(tr("No weekends"))
        set_tip(self.no_we, tr("This person is always off on the weekend days set in Settings."))
        f.addRow(tr("Fixed days off"), self.fixed)
        f.addRow("", self.fixed_hint)
        f.addRow("", self.fri)
        f.addRow("", self.no_we)
        col.addWidget(days)

        # shifts
        sh = QGroupBox(tr("Shifts"))
        f = QFormLayout(sh)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        self.allowed = QListWidget()
        self.allowed.setMinimumHeight(150)
        self.allowed.setMaximumHeight(190)
        set_tip(self.allowed, tr("Tick the shifts this person may work. Nothing ticked = every shift is allowed."))
        self.allowed.itemChanged.connect(self._allowed_changed)
        self.allowed_hint = QLabel(tr("Tick the shifts this person may work. Nothing ticked = any shift."))
        self.allowed_hint.setProperty("role", "hint")
        self.pref1 = QComboBox()
        self.pref2 = QComboBox()
        for c, tip in ((self.pref1, tr("The shift this person should get most often.")), (self.pref2, tr("Second choice (optional)."))):
            set_tip(c, tip)
            no_wheel(c)
            c.setMinimumWidth(220)
            c.currentIndexChanged.connect(self._pref_changed)
        f.addRow("", self.allowed_hint)
        f.addRow(tr("Allowed shifts"), self.allowed)
        f.addRow(tr("Preferred shift"), self.pref1)
        f.addRow(tr("Second preference"), self.pref2)
        col.addWidget(sh)

        # hours
        hr = QGroupBox(tr("Hours"))
        f = QFormLayout(hr)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        h = QHBoxLayout()
        self.max_h = QLineEdit()
        self.max_h.setMaximumWidth(150)
        set_tip(self.max_h, tr("This person's own weekly limit in hours. Leave blank to use the limit from Settings."))
        self.max_h.textEdited.connect(lambda _t: self._d_maxh.trigger())
        self.max_h.editingFinished.connect(self.flush)
        h.addWidget(self.max_h)
        u = QLabel(tr("hours per week"))
        u.setProperty("role", "unit")
        h.addWidget(u)
        h.addStretch(1)
        self.ot = QCheckBox(tr("Overtime allowed"))
        set_tip(self.ot, tr("This person may work overtime (the master switch in Settings must be on too)."))
        self.ot_rank = _rank_spin()
        self.ot_rank.setMaximumWidth(110)
        set_tip(self.ot_rank, tr(OT_RANK_HINT))
        self.ot_rank_hint = QLabel(tr(OT_RANK_HINT))
        self.ot_rank_hint.setProperty("role", "hint")
        self.ot_rank_hint.setWordWrap(True)
        f.addRow(tr("Max hours"), h)
        f.addRow("", self.ot)
        f.addRow(tr("OT priority"), self.ot_rank)
        f.addRow("", self.ot_rank_hint)
        col.addWidget(hr)

        nb = QGroupBox(tr("Notes"))
        v = QVBoxLayout(nb)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(70)
        set_tip(self.notes, tr("Anything worth remembering. Notes do not affect the roster."))
        self.notes.textChanged.connect(self._notes_typed)
        v.addWidget(self.notes)
        col.addWidget(nb)
        col.addStretch(1)

        self._d_name = self.debounce(self._commit_name, 500)
        self._d_maxh = self.debounce(self._commit_maxh, 500)
        self._d_notes = self.debounce(self._commit_notes, 700)

        for c in (self.gender,):
            c.currentIndexChanged.connect(lambda _i: self._write(gender=self.gender.currentData()))
        self.home.currentIndexChanged.connect(lambda _i: self._home_changed())
        self.also.currentIndexChanged.connect(lambda _i: self._also_changed())
        self.active.toggled.connect(lambda v: self._write(active=v))
        self.fri.toggled.connect(lambda v: self._write(fri=v))
        self.no_we.toggled.connect(lambda v: self._write(no_we=v))
        self.ot.toggled.connect(self._ot_toggled)
        self.ot_rank.valueChanged.connect(self._rank_changed)

    # ------------------------------------------------------------------ list
    def _emp_text(self, e, recs) -> str:
        g = {"M": tr("Male"), "F": tr("Female")}.get(e.gender, e.gender or "?")
        return f"{e.name}\n{recs.get(e.home_uid, '?')} · {g}" + ("" if e.active else tr(" · inactive"))

    def reload(self, select: int | None = None) -> None:
        view = self.ctx.view()
        keep = select if select is not None else self._uid
        recs = {r.uid: r.name for r in view.list_receptions()}
        self._recs = recs
        self.list.blockSignals(True)
        self.list.clear()
        for e in view.list_employees():
            it = QListWidgetItem(self._emp_text(e, recs))
            it.setData(Qt.ItemDataRole.UserRole, e.uid)
            it.setData(SEARCH_ROLE, _search_blob(e, recs))
            it.setIcon(_gender_icon(e.gender))
            if not e.active:
                it.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(it)
            if e.uid == keep:
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._filter()
        it = self.list.currentItem()
        self._uid = it.data(Qt.ItemDataRole.UserRole) if it else None
        self._fill_form()
        self.ot_panel.reload()
        self.apply_read_only(self.ctx.read_only)

    def _filter(self, *_a) -> None:
        q = fold(self.search.text())
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in fold(it.text().replace("\n", " ") + " " + (it.data(SEARCH_ROLE) or "")))

    def _selected(self, cur, _prev) -> None:
        self.flush()
        self._uid = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        self._loading = True
        try:
            self._fill_form()
        finally:
            self._loading = False
        self.apply_read_only(self.ctx.read_only)
        self.show_message("")

    def _update_item(self) -> None:
        it = self.list.currentItem()
        if it is None or self._uid is None:
            return
        e = self.ctx.view().get("employee", self._uid)
        it.setText(self._emp_text(e, self._recs))
        it.setData(SEARCH_ROLE, _search_blob(e, self._recs))
        it.setIcon(_gender_icon(e.gender))
        it.setForeground(Qt.GlobalColor.black if e.active else Qt.GlobalColor.gray)

    # ------------------------------------------------------------------ form
    def _fill_form(self) -> None:
        view = self.ctx.view()
        has = self._uid is not None
        self.form_host.setEnabled(has and not self.ctx.read_only)
        was = self._loading
        self._loading = True
        try:
            s = view.settings()
            self.max_h.setPlaceholderText(tr('{max_week_h:g} (from Settings)', max_week_h=s.max_week_h))
            self.fixed.set_consecutive(max(1, min(2, s.days_off_wk)))
            self._fill_combos()
            if not has:
                for w in (self.name, self.max_h):
                    w.clear()
                self.notes.setPlainText("")
                self.fixed.set_days([])
                self.allowed.clear()
                self.summary.setText(tr("No employee yet. Click 'Add' to create the first one."))
                self.summary.show()
                return
            e = view.get("employee", self._uid)
            self.name.setText(e.name)
            i = self.gender.findData(e.gender)
            if i < 0:
                self.gender.addItem(e.gender or "?", e.gender)
                i = self.gender.count() - 1
            self.gender.setCurrentIndex(i)
            self.home.setCurrentIndex(max(self.home.findData(e.home_uid), 0))
            self.also.setCurrentIndex(max(self.also.findData(e.also_uid), 0))
            self.active.setChecked(e.active)
            self.fixed.set_days([d for d in (e.fixed_off, e.fixed_off2) if d])
            self.fri.setChecked(e.fri)
            self.no_we.setChecked(e.no_we)
            self.allowed.clear()
            for sh in view.list_shifts():
                it = QListWidgetItem(disp_code(sh.code))
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if sh.uid in e.allowed else Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, sh.uid)
                self.allowed.addItem(it)
            self._fill_prefs(e.pref)
            self.max_h.setText("" if e.max_h is None else f"{e.max_h:g}")
            self.ot.setChecked(e.ot)
            self._fill_rank(e)
            self.notes.setPlainText(e.notes)
        finally:
            self._loading = was
        self.summary.hide()
        self.validate_form()

    def _fill_rank(self, e) -> None:
        """Show the OT priority of ``e``; the spin box is off (with a hint) while overtime is not allowed."""
        was = self._loading
        self._loading = True
        try:
            self.ot_rank.setValue(e.ot_rank if (e.ot and e.ot_rank) else 0)
            self.ot_rank.setEnabled(e.ot and not self.ctx.read_only)
            if e.ot:
                self.ot_rank_hint.setText(tr(OT_RANK_HINT))
            else:
                self.ot_rank_hint.setText(tr("Switch on 'Overtime allowed' to give this person an overtime priority.\n") + tr(OT_RANK_HINT))
        finally:
            self._loading = was

    def _ot_toggled(self, on: bool) -> None:
        if self._loading or self._uid is None:
            return
        old = self.ctx.view().get("employee", self._uid)
        if on or old.ot_rank is None:
            ok = self._write(ot=on)
        else:  # switching overtime off also removes the rank; both in one undo step
            ok = self._write(ot=False, ot_rank=None)
        if ok:
            self._fill_rank(self.ctx.view().get("employee", self._uid))

    def _rank_changed(self, v: int) -> None:
        if self._loading or self._uid is None:
            return
        if not self._write(ot_rank=v or None):
            self._fill_rank(self.ctx.view().get("employee", self._uid))

    def _switch_view(self, i: int) -> None:
        self.flush()
        if i == 1:
            self.ot_panel.reload()
        self.stack.setCurrentIndex(i)

    def _fill_combos(self) -> None:
        recs = self.ctx.view().list_receptions()
        for c, none in ((self.home, False), (self.also, True)):
            c.blockSignals(True)
            c.clear()
            if none:
                c.addItem(tr("— none —"), None)
            for r in recs:
                c.addItem(r.name, r.uid)
            c.blockSignals(False)

    def _allowed_uids(self) -> list[int]:
        return [self.allowed.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.allowed.count())
                if self.allowed.item(i).checkState() == Qt.CheckState.Checked]

    def _fill_prefs(self, pref: list[int]) -> None:
        shifts = {s.uid: disp_code(s.code) for s in self.ctx.view().list_shifts()}
        allowed = self._allowed_uids()
        pool = [u for u in shifts if (not allowed or u in allowed)]
        for c, cur in ((self.pref1, pref[0] if len(pref) > 0 else None), (self.pref2, pref[1] if len(pref) > 1 else None)):
            c.blockSignals(True)
            c.clear()
            c.addItem(tr("— none —"), None)
            for u in pool:
                c.addItem(shifts[u], u)
            if cur is not None and cur not in pool and cur in shifts:  # keep imported data visible; validation flags it
                c.addItem(shifts[cur] + tr("  (not allowed)"), cur)
            c.setCurrentIndex(max(c.findData(cur), 0))
            c.blockSignals(False)

    # ------------------------------------------------------------------ writes
    def _write(self, **fields) -> bool:
        if self._loading or self._uid is None:
            return False
        try:
            self.ctx.update("employee", self._uid, origin=self, **fields)
        except DuplicateError:
            msg = tr("There is already an employee called '{name}'.", name=fields.get("name", ""))
            set_state(self.name, msg)
            self.summary.setText(msg)
            self.summary.show()
            return False
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return False
        self.show_message("")
        self._update_item()
        self.validate_form()
        return True

    def _commit_name(self) -> None:
        name = self.name.text().strip()
        if not name:
            set_state(self.name, tr("A name is needed."))
            return
        if self._write(name=name):
            set_state(self.name, None)

    def _commit_maxh(self) -> None:
        v = self._parse_maxh()
        if v is False:
            self.validate_form()
            return
        self._write(max_h=v)

    def _parse_maxh(self):
        t = self.max_h.text().strip().replace(",", ".")
        if not t:
            return None
        try:
            v = float(t)
        except ValueError:
            return False
        return v if 0 < v <= 168 else False

    def _notes_typed(self) -> None:
        if not self._loading:
            self._d_notes.trigger()

    def _commit_notes(self) -> None:
        self._write(notes=self.notes.toPlainText())

    def _fixed_changed(self, _days) -> None:
        d = self.fixed.ordered_days()
        self._write(fixed_off=d[0] if d else "", fixed_off2=d[1] if len(d) > 1 else "")

    def _home_changed(self) -> None:
        if not self._loading:
            self._write(home_uid=self.home.currentData())

    def _also_changed(self) -> None:
        if not self._loading:
            self._write(also_uid=self.also.currentData())

    def _allowed_changed(self, _it) -> None:
        if self._loading or self._uid is None:
            return
        allowed = self._allowed_uids()
        pref = [u for u in (self.pref1.currentData(), self.pref2.currentData()) if u is not None and (not allowed or u in allowed)]
        self._write(allowed=allowed, pref=pref)
        self._loading = True
        try:
            self._fill_prefs(pref)
        finally:
            self._loading = False
        self.validate_form()

    def _pref_changed(self, _i) -> None:
        if self._loading:
            return
        pref = [u for u in (self.pref1.currentData(), self.pref2.currentData()) if u is not None]
        self._write(pref=list(dict.fromkeys(pref)))

    # ------------------------------------------------------------------ live validation
    def problems(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self._uid is None:
            return out
        s = self.ctx.view().settings()
        if not self.name.text().strip():
            out["name"] = tr("A name is needed.")
        if self.also.currentData() is not None and self.also.currentData() == self.home.currentData():
            out["also"] = tr("'Can also cover' is the same as the home reception.")
        fp = self.fixed.problem()
        if fp:
            out["fixed"] = fp
        weekend = {d.strip().upper() for d in s.weekend_days.split(",") if d.strip()}
        if self.no_we.isChecked() and self.fri.isChecked() and s.fri_name.upper() in weekend:
            out["no_we"] = tr("'No weekends' is ticked, but {fri_name} is a weekend day and this person can work Fridays. "
                              "Untick one of the two.", fri_name=tr(s.fri_name))
            out["fri"] = out["no_we"]
        if self._parse_maxh() is False:
            out["max_h"] = tr("Enter hours between 1 and 168, or leave blank to use the Settings value.")
        allowed = self._allowed_uids()
        for c, k in ((self.pref1, "pref1"), (self.pref2, "pref2")):
            u = c.currentData()
            if u is not None and allowed and u not in allowed:
                out[k] = tr("The preferred shift is not one of the allowed shifts.")
        return out

    def validate_form(self) -> bool:
        probs = self.problems()
        widgets = {"name": self.name, "also": self.also, "fixed": self.fixed, "no_we": self.no_we, "fri": self.fri,
                   "max_h": self.max_h, "pref1": self.pref1, "pref2": self.pref2}
        for k, w in widgets.items():
            set_state(w, probs.get(k))
        msgs = list(dict.fromkeys(probs.values()))
        self.summary.setText("\n".join(msgs))
        self.summary.setVisible(bool(msgs))
        return not msgs

    # ------------------------------------------------------------------ buttons
    def _unique_name(self, base: str) -> str:
        names = {e.name.upper() for e in self.ctx.view().list_employees()}
        name, n = base, 1
        while name.upper() in names:
            n += 1
            name = f"{base} {n}"
        return name

    def add_employee(self) -> None:
        self.flush()
        recs = self.ctx.view().list_receptions()
        if not recs:
            self.show_message(tr("Add a reception first (Receptions page) – every employee needs a home reception."))
            return
        cur = self.home.currentData() if self._uid is not None else None
        try:
            e = self.ctx.add("employee", origin=self, name=self._unique_name(tr("New employee")), gender="F",
                             home_uid=cur if cur in {r.uid for r in recs} else recs[0].uid)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self.search.clear()
        self.reload(select=e.uid)
        self.name.setFocus()
        self.name.selectAll()

    def duplicate_employee(self) -> None:
        if self._uid is None:
            return
        self.flush()
        from .context import record_fields

        src = self.ctx.view().get("employee", self._uid)
        f = record_fields(src)
        f["name"] = self._unique_name(tr("{src_name} (copy)", src_name=src.name))
        try:
            e = self.ctx.add("employee", origin=self, **f)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self.search.clear()
        self.reload(select=e.uid)
        self.name.setFocus()
        self.name.selectAll()

    def delete_employee(self) -> None:
        if self._uid is None:
            return
        self.flush()
        e = self.ctx.view().get("employee", self._uid)
        if not self.ctx.confirm(tr('Delete {name}? Their leave and fixed shifts are removed too.', name=e.name)):
            return
        try:
            self.ctx.delete("employee", self._uid, origin=self)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._uid = None
        self.reload()
        self.show_message("")

    def focus_record(self, record_id, field: str, message: str = "") -> None:
        """Select the employee ``record_id`` and mark the widget of ``field`` (red border + tooltip)."""
        self.tab_people.setChecked(True)
        self.stack.setCurrentIndex(0)
        if record_id is not None:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) == record_id:
                    self.search.clear()
                    self.list.setCurrentRow(i)
                    self.list.scrollToItem(self.list.item(i))
                    break
        w = {"name": self.name, "gender": self.gender, "home_uid": self.home, "also_uid": self.also, "active": self.active,
             "fixed_off": self.fixed, "fixed_off2": self.fixed, "fri": self.fri, "allowed": self.allowed, "pref": self.pref1,
             "max_h": self.max_h, "ot": self.ot, "ot_rank": self.ot_rank, "no_we": self.no_we, "notes": self.notes}.get(field)
        if field == "pref" and self.pref1.currentData() is None and self.pref2.currentData() is not None:
            w = self.pref2
        if w is not None:
            set_state(w, message or "Please check this field.")
            self.scroll.ensureWidgetVisible(w, 20, 60)

    def apply_read_only(self, ro: bool) -> None:
        self.add_btn.setEnabled(not ro)
        self.dup_btn.setEnabled(not ro and self._uid is not None)
        self.del_btn.setEnabled(not ro and self._uid is not None)
        self.form_host.setEnabled(not ro and self._uid is not None)
        self.fixed.set_read_only(ro)
        if self._uid is not None:
            try:
                self.ot_rank.setEnabled(self.ot.isChecked() and not ro)
            except RuntimeError:
                pass
        self.ot_panel.set_read_only(ro)
