"""Settings screen: every general rule of the roster with plain labels, units and tooltips."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..i18n import tr, tr_msg
from ..store import StoreError
from .base import Page
from .widgets import WEEKDAYS, TimePicker, WeekdayChips, no_wheel, set_state, set_tip

_DAY_NAMES = {"SUN": "Sunday", "MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday", "FRI": "Friday", "SAT": "Saturday"}


class SettingsPage(Page):
    key = "settings"
    title = "Settings"
    subtitle = "General rules for every roster. Changes are saved as you make them."
    watches = frozenset({"settings"})

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.fields: dict[str, QWidget] = {}
        self.hints: dict[str, QLabel] = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("pageBody")
        scroll.setWidget(host)
        self.body.addWidget(scroll, 1)
        cols = QHBoxLayout(host)
        cols.setContentsMargins(0, 0, 8, 0)
        cols.setSpacing(16)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(8)
        right.setSpacing(8)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)

        g = self._group(left, tr("Week"))
        self._combo(g, "week_start_name", tr("Week starts on"), [(tr(_DAY_NAMES[d]), d) for d in WEEKDAYS],
                    tr("The first day of each working week. Weekly hour limits are counted from this day."))
        self._combo(g, "fri_name", tr("Day treated as Friday"), [(tr(_DAY_NAMES[d]), d) for d in WEEKDAYS],
                    tr("The weekday that has the 'Friday' rules: 'Weekdays (not Friday)' in Coverage skips it, and "
                    "employees who cannot work Fridays are off on it."))

        g = self._group(left, tr("Hours"))
        self._spin(g, "max_week_h", tr("Normal hours per week"), 1, 168, 0.5, tr(" h"),
                   "Most normal (non-overtime) hours one person works in a week. An employee's own limit overrides this.")
        self._spin(g, "max_shift_h", tr("Longest shift"), 1, 24, 0.5, tr(" h"), "Most hours in a single shift.")
        self._spin(g, "ext_max", tr("Longest extension"), 0, 24, 0.5, tr(" h"),
                   "A shift may be extended before or after by up to this many hours. Only the extra hours count as overtime.")
        self._spin(g, "carry_in", tr("Day 1 already covered"), 0, 24, 0.5, tr(" h"),
                   "Hours at the start of the first day that last month's night shifts already cover.")

        g = self._group(left, tr("Rest"))
        self._spin(g, "min_rest", tr("Rest between shifts"), 0, 48, 0.5, tr(" h"),
                   "Minimum hours of rest between a shift and the next one on the following day.")
        self._spin(g, "min_rest_split", tr("Rest inside a split shift"), 0, 24, 0.5, tr(" h"),
                   "Minimum gap between the two parts of a split shift.")

        g = self._group(right, tr("Men-only hours"))
        self._time(g, "male_from", tr("Men only from"),
                   tr("From this time of day only men may work (for example 10 PM)."))
        self._time(g, "male_to", tr("Men only until"),
                   tr("Until this time of day only men may work (for example 06 AM). Women may work until the window starts."))

        g = self._group(right, tr("Days off"))
        self._spin(g, "days_off_wk", tr("Days off per week"), 1, 4, 1, tr(" days"),
                   "Every employee gets one block of this many days off, repeating every week.", integer=True)
        chips = WeekdayChips()
        self.fields["weekend_days"] = chips
        set_tip(chips, tr("Days that count as the weekend. Employees marked 'no weekends' are off on these days."))
        chips.changed.connect(lambda days: self._edited("weekend_days"))
        self._row(g, "weekend_days", tr("Weekend days"), chips)

        g = self._group(right, tr("Overtime"))
        self._check(g, "ot_on", tr("Overtime allowed"), tr("Master switch. When off, nobody works overtime, whatever their own setting says."))
        self._spin(g, "ot_max", tr("Overtime limit per week"), 0, 168, 1, tr(" h"), "Most overtime hours one person may work in a week.")
        self._check(g, "ot_keep", tr("Keep one free day per week"),
                    tr("Even with overtime, every person keeps at least one completely free day each week."))
        self._check(g, "strict_on", tr("Strict days-off rule"),
                    tr("On: each person's block of days off repeats every week and is never moved, "
                    "it can only be worked as overtime."))
        right.addStretch(1)
        left.addStretch(1)

    # ------------------------------------------------------------------ builders
    def _group(self, parent: QVBoxLayout, title: str) -> QGridLayout:
        box = QGroupBox(title)
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 190)
        parent.addWidget(box)
        return grid

    def _row(self, grid: QGridLayout, key: str, label: str, widget: QWidget, unit: str = "") -> None:
        r = grid.rowCount()
        lab = QLabel(label)
        lab.setBuddy(widget)
        grid.addWidget(lab, r, 0, Qt.AlignmentFlag.AlignTop)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(widget)
        if unit:
            u = QLabel(unit)
            u.setProperty("role", "unit")
            h.addWidget(u)
        h.addStretch(1)
        grid.addWidget(w, r, 1)
        hint = QLabel("")
        hint.setProperty("role", "error")
        hint.setWordWrap(True)
        hint.hide()
        grid.addWidget(hint, r + 1, 1)
        self.hints[key] = hint
        lab.setToolTip(widget.toolTip())

    def _spin(self, grid, key, label, lo, hi, step, suffix, tip, integer=False) -> None:
        if integer:
            s = QSpinBox()
            s.setRange(int(lo), int(hi))
        else:
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(1)
            s.setSingleStep(step)
        s.setKeyboardTracking(False)
        s.setMinimumWidth(110)
        no_wheel(s)
        set_tip(s, tip)
        self.fields[key] = s
        s.valueChanged.connect(lambda _v, k=key: self._edited(k))
        self._row(grid, key, label, s, suffix.strip())

    def _combo(self, grid, key, label, items, tip) -> None:
        c = QComboBox()
        for text, data in items:
            c.addItem(text, data)
        c.setMinimumWidth(150)
        no_wheel(c)
        set_tip(c, tip)
        self.fields[key] = c
        c.currentIndexChanged.connect(lambda _i, k=key: self._edited(k))
        self._row(grid, key, label, c)

    def _time(self, grid, key, label, tip) -> None:
        t = TimePicker()
        set_tip(t, tip)
        self.fields[key] = t
        t.valueChanged.connect(lambda _v, k=key: self._edited(k))
        self._row(grid, key, label, t)

    def _check(self, grid, key, label, tip) -> None:
        c = QCheckBox(label)
        set_tip(c, tip)
        self.fields[key] = c
        c.toggled.connect(lambda _v, k=key: self._edited(k))
        r = grid.rowCount()
        grid.addWidget(c, r, 0, 1, 2)
        hint = QLabel(tip)
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        grid.addWidget(hint, r + 1, 0, 1, 2)
        self.hints[key] = QLabel("")  # unused placeholder so validate() can address every key
        self.hints[key].hide()

    # ------------------------------------------------------------------ values
    def get(self, key: str):
        w = self.fields[key]
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QSpinBox):
            return int(w.value())
        if isinstance(w, QDoubleSpinBox):
            return float(w.value())
        if isinstance(w, TimePicker):
            return w.value()
        if isinstance(w, QComboBox):
            return w.currentData()
        if isinstance(w, WeekdayChips):
            return ",".join(w.days())
        raise KeyError(key)

    def set(self, key: str, v) -> None:
        w = self.fields[key]
        if isinstance(w, QCheckBox):
            w.setChecked(bool(v))
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.setValue(v)
        elif isinstance(w, TimePicker):
            w.setValue(v)
        elif isinstance(w, QComboBox):
            i = w.findData(str(v).upper())
            w.setCurrentIndex(max(i, 0))
        elif isinstance(w, WeekdayChips):
            w.set_days([p for p in str(v).split(",") if p.strip()])

    def reload(self) -> None:
        s = self.ctx.view().settings()
        for k in self.fields:
            w = self.fields[k]
            w.blockSignals(True)
            for ch in w.findChildren(QWidget):
                ch.blockSignals(True)
            self.set(k, getattr(s, k))
            w.blockSignals(False)
            for ch in w.findChildren(QWidget):
                ch.blockSignals(False)
        self.validate()

    def apply_read_only(self, ro: bool) -> None:
        for k, w in self.fields.items():
            if isinstance(w, WeekdayChips):
                w.set_read_only(ro)
            else:
                w.setEnabled(not ro)
        if not ro:
            self.validate()

    def _edited(self, key: str) -> None:
        if self._loading:
            return
        self.validate()
        try:
            self.ctx.update_settings(origin=self, **{key: self.get(key)})
            self.show_message("")
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))

    def focus_record(self, record_id, field: str, message: str = "") -> None:
        """Mark the setting ``field`` (red border + tooltip) and scroll to it."""
        w = self.fields.get(field)
        if w is None:
            return
        set_state(w, message or "Please check this field.")
        sa = self.findChild(QScrollArea)
        if sa is not None:
            sa.ensureWidgetVisible(w, 20, 60)
        w.setFocus()

    # ------------------------------------------------------------------ live validation
    def problems(self) -> dict[str, tuple[str, str]]:
        """``{field: (level, message)}`` for the values currently shown."""
        g = self.get
        out: dict[str, tuple[str, str]] = {}
        if g("min_rest") < 8:
            out["min_rest"] = ("warn", tr("Less than 8 hours of rest is unusual – the input check will flag it."))
        if g("male_from") == g("male_to"):
            out["male_to"] = ("error", tr("The men-only window starts and ends at the same time – choose different times."))
            out["male_from"] = out["male_to"]
        if g("max_shift_h") > g("max_week_h"):
            out["max_shift_h"] = ("error", tr("A shift cannot be longer than the weekly limit."))
        if g("ext_max") > g("max_shift_h"):
            out["ext_max"] = ("warn", tr("The extension is longer than a whole shift."))
        if g("carry_in") > 24:
            out["carry_in"] = ("error", tr("At most 24 hours."))
        shift = max(g("max_shift_h"), 0.5)
        need = max(g("days_off_wk"), 7 - math.floor(g("max_week_h") / shift))
        if need > g("days_off_wk"):
            out["days_off_wk"] = ("warn", tr("With {v0:g} h per week and shifts of up to {shift:g} h, each person needs {need} days off.",
                                             v0=g("max_week_h"), shift=shift, need=need))
        if g("ot_on") and g("ot_max") <= 0:
            out["ot_max"] = ("warn", tr("Overtime is allowed, but the weekly limit is 0 hours."))
        return out

    def validate(self) -> bool:
        probs = self.problems()
        if "ot_max" in self.fields:
            self.fields["ot_max"].setEnabled(self.fields["ot_on"].isChecked() and not self.ctx.read_only)
        for k, w in self.fields.items():
            lvl, msg = probs.get(k, ("", ""))
            set_state(w, msg or None, lvl or "error")
            h = self.hints.get(k)
            if h is not None and not isinstance(self.fields[k], QCheckBox):
                h.setProperty("role", "warn" if lvl == "warn" else "error")
                h.style().unpolish(h)
                h.style().polish(h)
                h.setText(msg)
                h.setVisible(bool(msg))
        return not any(lv == "error" for lv, _m in probs.values())
