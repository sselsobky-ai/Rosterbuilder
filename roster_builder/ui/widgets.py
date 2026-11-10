"""Reusable widgets: weekday chips, time picker, 24-hour coverage bar, validation helper."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QPushButton, QSizePolicy, QToolTip, QWidget

from .. import i18n
from ..i18n import count_phrase, disp_hour, tr
from ..model import hour_txt
from . import theme

WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]


# ----------------------------------------------------------------------------- validation helper
def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_state(w: QWidget, message: str | None = None, level: str = "error") -> bool:
    """Mark ``w`` invalid (red border, ``level="error"``) or as a warning (amber, ``level="warn"``) with the
    message as tooltip; ``message=None`` / ``""`` clears it and restores the normal tooltip.  Returns True when valid."""
    message = tr(message) if message else message
    base = w.property("_base_tip")
    if base is None:
        base = w.toolTip()
        w.setProperty("_base_tip", base)
    if message:
        w.setProperty("state", level)
        w.setToolTip(message + (("\n\n" + base) if base else ""))
    else:
        w.setProperty("state", "")
        w.setToolTip(base)
    repolish(w)
    return not message


def set_tip(w: QWidget, tip: str) -> None:
    """Set the normal tooltip of a widget that may get validation messages."""
    tip = tr(tip) if tip else tip
    w.setProperty("_base_tip", tip)
    if not w.property("state"):
        w.setToolTip(tip)


def state_of(w: QWidget) -> str:
    return w.property("state") or ""


class _NoWheel(QObject):
    """Event filter: a wheel turn over a field that has no focus scrolls the page instead of changing the value."""

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Type.Wheel and not obj.hasFocus():
            ev.ignore()
            return True
        return False


_NOWHEEL = _NoWheel()


def no_wheel(w: QWidget) -> QWidget:
    w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    w.installEventFilter(_NOWHEEL)
    return w


# ----------------------------------------------------------------------------- weekdays
def expand_days(days: str, fri_name: str = "FRI") -> set[str]:
    """Weekday names of a coverage ``days`` text (ALL / WEEKDAYS = all but Friday / comma list)."""
    x = (days or "").strip().upper()
    if x in ("", "ALL"):
        return set(WEEKDAYS)
    if x == "WEEKDAYS":
        return {d for d in WEEKDAYS if d != fri_name.strip().upper()}
    return {p.strip() for p in x.split(",") if p.strip() in WEEKDAYS}


def is_consecutive(days) -> bool:
    """True when the weekdays form one block of neighbouring days (SAT and SUN are neighbours)."""
    idx = {WEEKDAYS.index(d) for d in days if d in WEEKDAYS}
    if len(idx) <= 1 or len(idx) == 7:
        return True
    return sum(1 for i in idx if (i - 1) % 7 not in idx) == 1


def block_order(days) -> list[str]:
    """The days of a consecutive block in order from its first day (``[SAT, SUN]`` stays in that order)."""
    idx = {WEEKDAYS.index(d) for d in days if d in WEEKDAYS}
    if not idx:
        return []
    if len(idx) == 7 or not is_consecutive(days):
        return [d for d in WEEKDAYS if WEEKDAYS.index(d) in idx]
    start = next(i for i in sorted(idx) if (i - 1) % 7 not in idx)
    return [WEEKDAYS[(start + k) % 7] for k in range(len(idx))]


class WeekdayChips(QFrame):
    """Seven toggle chips SUN..SAT.

    ``set_consecutive(max_n)`` switches on the "one block of neighbouring days, at most ``max_n``" mode:
    a pick that would break the rule is refused (the chip springs back) and ``rejected(message)`` is emitted.
    ``changed(list)`` is emitted after every accepted user click.
    """

    changed = Signal(list)
    rejected = Signal(str)

    def __init__(self, parent=None, names: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setProperty("chips", True)
        self._max: int | None = None
        self._consecutive = False
        self._read_only = False
        self.buttons: dict[str, QPushButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(6)
        for d in names or WEEKDAYS:
            b = QPushButton(tr(d))
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked, day=d: self._clicked(day, checked))
            lay.addWidget(b)
            self.buttons[d] = b
        lay.addStretch(1)

    # -- configuration
    def set_consecutive(self, max_n: int | None, on: bool = True) -> None:
        self._consecutive = on
        self._max = max_n

    def set_read_only(self, ro: bool) -> None:
        self._read_only = ro
        for b in self.buttons.values():
            b.setEnabled(not ro)

    # -- value
    def days(self) -> list[str]:
        """Selected days in SUN..SAT order."""
        return [d for d in WEEKDAYS if d in self.buttons and self.buttons[d].isChecked()]

    def ordered_days(self) -> list[str]:
        """Selected days from the first day of the block (consecutive mode) else SUN..SAT order."""
        return block_order(self.days())

    def set_days(self, days) -> None:
        sel = {str(d).strip().upper() for d in days}
        for d, b in self.buttons.items():
            b.blockSignals(True)
            b.setChecked(d in sel)
            b.blockSignals(False)

    # -- validation
    def problem(self, days=None) -> str:
        days = self.days() if days is None else days
        if self._consecutive and self._max is not None and len(days) > self._max:
            return tr("At most {c} off per week (see Settings).", c=count_phrase(self._max, "day"))
        if self._consecutive and not is_consecutive(days):
            return tr("Days off must be next to each other (one block).")
        return ""

    def is_valid(self) -> bool:
        return not self.problem()

    def _clicked(self, day: str, checked: bool) -> None:
        if self._read_only:
            return
        if self._consecutive and checked:
            msg = self.problem(self.days())
            if msg:
                b = self.buttons[day]
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
                set_state(self, msg)
                if self.isVisible():
                    QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), msg, self)
                self.rejected.emit(msg)
                return
        set_state(self, None)
        self.changed.emit(self.days())


# ----------------------------------------------------------------------------- time picker
class TimePicker(QComboBox):
    """Time of day in 30-minute steps shown as 12-hour text ("08 PM", "12 MN", "06:30 AM")."""

    valueChanged = Signal(float)

    def __init__(self, lo: float = 0.0, hi: float = 23.5, step: float = 0.5, parent=None) -> None:
        super().__init__(parent)
        v = lo
        while v <= hi + 1e-9:
            self.addItem(disp_hour(hour_txt(v)), v)
            v += step
        self.setMaxVisibleItems(12)
        self.setMinimumWidth(104)
        no_wheel(self)
        self.currentIndexChanged.connect(lambda _i: self.valueChanged.emit(self.value()))

    def value(self) -> float:
        return float(self.currentData())

    def setValue(self, v: float) -> None:  # noqa: N802 - Qt style
        v = float(v)
        for i in range(self.count()):
            if abs(self.itemData(i) - v) < 1e-9:
                self.setCurrentIndex(i)
                return
        pos = next((i for i in range(self.count()) if self.itemData(i) > v), self.count())
        self.blockSignals(True)
        self.insertItem(pos, disp_hour(hour_txt(v)), v)
        self.blockSignals(False)
        self.setCurrentIndex(pos)


# ----------------------------------------------------------------------------- 24-hour bar
class HourBar24(QWidget):
    """One 24-hour strip per weekday: the number in each hour is the minimum staff there, the green strip below
    it means "at least one man needed"; hours that no coverage row covers are red."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.staff: list[list[float]] = [[0.0] * 24 for _ in range(7)]
        self.men: list[list[float]] = [[0.0] * 24 for _ in range(7)]
        self.has: list[list[bool]] = [[False] * 24 for _ in range(7)]
        self.setMinimumSize(760, 20 + 7 * 28 + 4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    # rows: iterable of (frm, to, min_staff, min_male, days_text)
    def set_rows(self, rows, fri_name: str = "FRI") -> None:
        self.staff = [[0.0] * 24 for _ in range(7)]
        self.men = [[0.0] * 24 for _ in range(7)]
        self.has = [[False] * 24 for _ in range(7)]
        for frm, to, mn, mm, days in rows:
            if frm is None or to is None or to <= frm:
                continue
            for d in expand_days(days, fri_name):
                j = WEEKDAYS.index(d)
                for h in range(24):
                    if frm < h + 1 and to > h:
                        self.has[j][h] = True
                        self.staff[j][h] = max(self.staff[j][h], float(mn or 0))
                        self.men[j][h] = max(self.men[j][h], float(mm or 0))
        self.update()

    def uncovered_hours(self, day: str) -> list[int]:
        j = WEEKDAYS.index(day)
        return [h for h in range(24) if not self.has[j][h]]

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize

        return QSize(900, 20 + 7 * 28 + 4)

    # geometry
    _TOP = 20
    _ROW = 28

    @property
    def _LABEL_W(self) -> int:  # noqa: N802 - the Arabic day names are wider
        return 58 if i18n.is_ar() else 48

    def _cell_w(self) -> float:
        return max(20.0, (self.width() - self._LABEL_W - 2) / 24.0)

    def _x(self, h: float) -> float:
        """Left edge of hour ``h`` (right to left in a mirrored layout: midnight at the right)."""
        if i18n.is_rtl():
            return self.width() - self._LABEL_W - (h + 1) * self._cell_w() + 0.0
        return self._LABEL_W + h * self._cell_w()

    def _edge(self, h: float) -> float:
        """X of the boundary before hour ``h`` (grid ticks)."""
        if i18n.is_rtl():
            return self.width() - self._LABEL_W - h * self._cell_w()
        return self._LABEL_W + h * self._cell_w()

    @staticmethod
    def _hour_label(h: int) -> str:
        if i18n.is_ar():
            h12 = h % 12 or 12
            return f"{h12}{'ص' if h < 12 else 'م'}"
        return hour_txt(h).replace(" ", "").lower().replace("mn", "a").replace("am", "a").replace("pm", "p").lstrip("0")

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cw = self._cell_w()
        small = QFont(self.font())
        small.setPointSizeF(8.5)
        p.setFont(small)
        p.setPen(QColor(theme.MUTED))
        for h in range(0, 24, 3):
            if i18n.is_rtl():
                p.drawText(QRectF(self._x(h + 2), 0, cw * 3, self._TOP - 2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, self._hour_label(h))
            else:
                p.drawText(QRectF(self._x(h), 0, cw * 3, self._TOP - 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self._hour_label(h))
        shades = ["#E5E7EB", "#BFDBFE", "#93C5FD", "#60A5FA", "#3B82F6"]
        bold = QFont(small)
        bold.setBold(True)
        for j, d in enumerate(WEEKDAYS):
            y = self._TOP + j * self._ROW
            p.setFont(small)
            p.setPen(QColor(theme.TEXT))
            if i18n.is_rtl():
                p.drawText(QRectF(self.width() - self._LABEL_W + 6, y, self._LABEL_W - 6, self._ROW - 4),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, tr(d))
            else:
                p.drawText(QRectF(0, y, self._LABEL_W - 6, self._ROW - 4), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, d)
            for h in range(24):
                x = self._x(h)
                r = QRectF(x + 1, y + 1, cw - 2, 18)
                if not self.has[j][h]:
                    p.setPen(QPen(QColor(theme.ERROR), 1))
                    p.setBrush(QColor("#FECACA"))
                    p.drawRoundedRect(r, 3, 3)
                    continue
                n = self.staff[j][h]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(shades[min(int(n), 4)]))
                p.drawRoundedRect(r, 3, 3)
                if n > 0:
                    p.setFont(bold)
                    p.setPen(QColor("white" if n >= 3 else theme.TEXT))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{n:g}")
                if self.men[j][h] > 0:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor("#16A34A"))
                    p.drawRoundedRect(QRectF(x + 1, y + 20, cw - 2, 5), 2, 2)
        # hour grid ticks every 3 hours
        p.setPen(QPen(QColor("#9CA3AF"), 1, Qt.PenStyle.DotLine))
        for h in range(0, 25, 3):
            x = self._edge(h)
            p.drawLine(QPointF(x, self._TOP - 2), QPointF(x, self._TOP + 7 * self._ROW))

    def event(self, ev):
        if ev.type() == QEvent.Type.ToolTip:
            pos = ev.pos()
            h = int(((self.width() - self._LABEL_W - pos.x()) if i18n.is_rtl() else (pos.x() - self._LABEL_W)) // self._cell_w())
            j = int((pos.y() - self._TOP) // self._ROW)
            if 0 <= h < 24 and 0 <= j < 7:
                if not self.has[j][h]:
                    txt = tr("{v0} {v1}: no coverage target", v0=tr(WEEKDAYS[j]), v1=disp_hour(hour_txt(h)))
                else:
                    txt = tr("{v0} {v1}: at least {v2:g} staff, {v3:g} men", v0=tr(WEEKDAYS[j]), v1=disp_hour(hour_txt(h)),
                             v2=self.staff[j][h], v3=self.men[j][h])
                QToolTip.showText(ev.globalPos(), txt, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)
