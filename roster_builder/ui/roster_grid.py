"""Widgets of the Roster page: the cell model, the painted cells (colours of BUILD_GUIDE section 9), the two-level
employee header, the frozen day header, the coverage strip and the cell editor dialog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLabel,
                               QSpinBox, QStyle, QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import disp_cell, disp_code, strf, tr
from . import theme

# colours of the schedule (BUILD_GUIDE section 9, the same as the Excel / PDF export)
FILL = {"ot": "#FF8080", "fixed": "#A9D08E", "helper": "#9DC3E6", "leave": "#CDB4E8", "off": "#D9D9D9",
        "work": "#FFFFFF", "empty": "#FFFFFF"}
WEEKEND_TINT = {"work": "#FFF4D6", "empty": "#FFF4D6"}
BAND_COLOURS = ["#1F3864", "#3B5B7E", "#4A4E69"]
GRID = QColor("#C9CFD8")
PROBLEM = QColor("#DC2626")
WARNING = QColor("#D97706")  # manual exception (overtime on a normal day)
NOTE = QColor("#2563EB")  # manual choice that breaks no rule (extra day off)

KIND_ROLE = Qt.ItemDataRole.UserRole + 1
LOCK_ROLE = Qt.ItemDataRole.UserRole + 2
PROBLEM_ROLE = Qt.ItemDataRole.UserRole + 3
WEEKEND_ROLE = Qt.ItemDataRole.UserRole + 4
NOTE_ROLE = Qt.ItemDataRole.UserRole + 5  # [(severity, sentence)] of warnings / info for the cell

LEGEND = [("ot", "Overtime / extension"), ("fixed", "Fixed shift"), ("helper", "Helping at another reception"),
          ("leave", "Leave"), ("off", "Day off (OFF)")]


@dataclass
class Col:
    key: str  # employee key (upper case)
    name: str
    gender: str
    rec: str
    uid: int


class RosterModel(QAbstractTableModel):
    """Rows = days of the month, columns = employees grouped by home reception."""

    def __init__(self) -> None:
        super().__init__()
        self.cols: list[Col] = []
        self.days: list[date] = []
        self.cells: dict = {}  # (employee name, date) -> report.Cell
        self.locked: set = set()  # (employee key, date)
        self.problems: dict = {}  # (employee key, date) -> [sentences]
        self.notes: dict = {}  # (employee key, date) -> [(severity, sentence)] warnings and info, no red mark
        self.weekend: set = set()
        self.groups: list[tuple[str, int, int]] = []  # (reception, first col, last col)
        self.group_of: list[int] = []
        self.zoom = 1.0

    # ------------------------------------------------------------------ data
    def set_data(self, cols: list[Col], days: list[date], cells: dict, locked: set, problems: dict, weekend: set) -> None:
        self.beginResetModel()
        self.cols, self.days, self.cells, self.locked, self.problems, self.weekend = cols, days, cells, locked, problems, weekend
        self.groups = []
        self.group_of = []
        for i, c in enumerate(cols):
            if self.groups and self.groups[-1][0] == c.rec:
                g = self.groups[-1]
                self.groups[-1] = (g[0], g[1], i)
            else:
                self.groups.append((c.rec, i, i))
            self.group_of.append(len(self.groups) - 1)
        self.endResetModel()

    def update_data(self, cells: dict, locked: set, problems: dict) -> None:
        """Same employees and days, new cell content (after an edit): no reset, so selection and scroll stay."""
        self.cells, self.locked, self.problems = cells, locked, problems
        if self.cols and self.days:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.days) - 1, len(self.cols) - 1))

    def col_of(self, emp_key: str) -> int:
        for i, c in enumerate(self.cols):
            if c.key == emp_key:
                return i
        return -1

    def row_of(self, d: date) -> int:
        try:
            return self.days.index(d)
        except ValueError:
            return -1

    def cell(self, row: int, col: int):
        return self.cells.get((self.cols[col].name, self.days[row]))

    # ------------------------------------------------------------------ Qt model
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.days)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.cols)

    def flags(self, index):  # noqa: D401
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        c, d = self.cols[index.column()], self.days[index.row()]
        cell = self.cells.get((c.name, d))
        key = (c.key, d)
        if role == Qt.ItemDataRole.DisplayRole:
            return disp_cell(cell.text) if cell else ""
        if role == KIND_ROLE:
            return cell.kind if cell else "empty"
        if role == LOCK_ROLE:
            return key in self.locked
        if role == PROBLEM_ROLE:
            return self.problems.get(key, [])
        if role == NOTE_ROLE:
            return self.notes.get(key, [])
        if role == WEEKEND_ROLE:
            return d in self.weekend
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [tr("{name} – {v0}", name=c.name, v0=strf(d, "%A %d %B"))]
            if cell and cell.text:
                lines.append(disp_cell(cell.text))
            if key in self.locked:
                lines.append(tr("📌 Pinned: kept when you rebuild with “Keep my manual changes”."))
            for p in self.problems.get(key, []):
                lines.append("⚠ " + p)
            for sev, p in self.notes.get(key, []):
                lines.append(("⚠ " if sev == "warning" else "ℹ ") + p)
            return "\n".join(lines)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{c.name} {strf(d, '%d %b')} {disp_cell(cell.text) if cell else ''}"
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return self.cols[section].name if 0 <= section < len(self.cols) else None
            return strf(self.days[section], "%a %d %b") if 0 <= section < len(self.days) else None
        return None


def font_for(base: QFont, zoom: float, bold: bool = False, size: float = 8.5) -> QFont:
    f = QFont(base)
    f.setPointSizeF(max(5.5, size * zoom))
    f.setBold(bold)
    return f


class RosterDelegate(QStyledItemDelegate):
    """Paints a cell: colour of its kind, wrapped text, pin for pinned cells, red outline + corner for broken rules."""

    def __init__(self, view) -> None:
        super().__init__(view)
        self.view = view
        self.zoom = 1.0

    def font(self, kind: str) -> QFont:
        return font_for(self.view.font(), self.zoom, bold=kind == "ot")

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        w = self.view.columnWidth(index.column())
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        kind = index.data(KIND_ROLE)
        fm = QFontMetrics(self.font(kind))
        r = fm.boundingRect(QRect(0, 0, max(20, w - 10), 10000), int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter), text)
        return QSize(w, max(int(34 * self.zoom), r.height() + int(12 * self.zoom)))

    def paint(self, p: QPainter, opt, index) -> None:
        kind = index.data(KIND_ROLE) or "empty"
        rect: QRect = opt.rect
        bg = QColor(WEEKEND_TINT.get(kind, FILL.get(kind, "#FFFFFF"))) if index.data(WEEKEND_ROLE) else QColor(FILL.get(kind, "#FFFFFF"))
        p.save()
        p.fillRect(rect, bg)
        p.setPen(QPen(GRID, 1))
        rtl = opt.direction == Qt.LayoutDirection.RightToLeft
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        p.drawLine(rect.topLeft() if rtl else rect.topRight(), rect.bottomLeft() if rtl else rect.bottomRight())
        model: RosterModel = index.model()
        if index.column() == 0 or model.group_of[index.column()] != model.group_of[index.column() - 1]:
            p.setPen(QPen(QColor("#6B7280"), 2))  # the start edge of a reception group
            p.drawLine(rect.topRight() if rtl else rect.topLeft(), rect.bottomRight() if rtl else rect.bottomLeft())
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if text:
            p.setFont(self.font(kind))
            p.setPen(QColor("#6B7280") if kind == "off" else QColor("#111827"))
            p.drawText(rect.adjusted(4, 2, -4, -2), int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter), text)
        z = self.zoom
        if index.data(LOCK_ROLE):
            self._pin(p, QPointF(rect.left() + 9 * z + 3 if rtl else rect.right() - 9 * z - 3, rect.top() + 9 * z + 2), 5.0 * z)
        if index.data(PROBLEM_ROLE):
            p.setPen(QPen(PROBLEM, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(1, 1, -2, -2))
            tri = int(11 * z) + 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PROBLEM)
            p.drawPolygon(self._corner(rect, tri, rtl))
        elif index.data(NOTE_ROLE):
            notes = index.data(NOTE_ROLE)
            col = WARNING if any(sv == "warning" for sv, _t in notes) else NOTE
            tri = int(11 * z) + 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPolygon(self._corner(rect, tri, rtl))
        if opt.state & QStyle.StateFlag.State_Selected:
            p.setPen(QPen(QColor(theme.ACCENT), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(1, 1, -2, -2))
        p.restore()

    @staticmethod
    def _corner(rect: QRect, tri: int, rtl: bool) -> QPolygonF:
        """The little triangle in the start corner of a cell (top left; top right when mirrored)."""
        if rtl:
            x = rect.right()
            return QPolygonF([QPointF(x, rect.top() + 1), QPointF(x - tri, rect.top() + 1), QPointF(x, rect.top() + 1 + tri)])
        return QPolygonF([QPointF(rect.left() + 1, rect.top() + 1), QPointF(rect.left() + 1 + tri, rect.top() + 1),
                          QPointF(rect.left() + 1, rect.top() + 1 + tri)])

    @staticmethod
    def _pin(p: QPainter, c: QPointF, r: float) -> None:
        """A small push-pin: round blue head with a white rim, a needle and a highlight."""
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#374151"), max(1.2, r / 3.0)))
        p.drawLine(QPointF(c.x() - r * 0.3, c.y() + r * 0.3), QPointF(c.x() - r * 1.5, c.y() + r * 1.7))
        p.setPen(QPen(QColor("white"), max(1.0, r / 4.0)))
        p.setBrush(QColor("#1D4ED8"))
        p.drawEllipse(c, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 170))
        p.drawEllipse(QPointF(c.x() - r * 0.3, c.y() - r * 0.3), r * 0.3, r * 0.3)


class GroupHeader(QHeaderView):
    """Employee header with two levels: the reception band on top, name and gender below."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.zoom = 1.0
        self.setSectionsClickable(False)
        self.setHighlightSections(False)

    def band_h(self) -> int:
        return int(24 * self.zoom)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), self.band_h() + int(40 * self.zoom))

    def paintSection(self, p: QPainter, rect: QRect, i: int) -> None:  # noqa: N802
        model: RosterModel = self.model()
        if not isinstance(model, RosterModel) or i >= len(model.cols):
            return
        col = model.cols[i]
        gi = model.group_of[i]
        rec, first, last = model.groups[gi]
        bh = self.band_h()
        p.save()
        p.setClipRect(rect)
        band = QRect(rect.left(), rect.top(), rect.width(), bh)
        p.fillRect(band, QColor(BAND_COLOURS[gi % len(BAND_COLOURS)]))
        # the reception name is centred over the visible part of its columns
        a, b = self.sectionViewportPosition(first), self.sectionViewportPosition(last)
        left = min(a, b)  # (a mirrored header lists the first column at the right)
        right = max(a + self.sectionSize(first), b + self.sectionSize(last))
        vis_l, vis_r = max(left, 0), min(right, self.viewport().width())
        p.setFont(font_for(self.font(), self.zoom, True, 8.5))
        p.setPen(QColor("white"))
        p.drawText(QRect(vis_l, rect.top(), max(0, vis_r - vis_l), bh), int(Qt.AlignmentFlag.AlignCenter), rec)
        body = QRect(rect.left(), rect.top() + bh, rect.width(), rect.height() - bh)
        p.fillRect(body, QColor("#EEF1F5"))
        p.setPen(QPen(GRID, 1))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        p.drawLine(body.bottomLeft(), body.bottomRight())
        p.drawLine(body.topLeft() if rtl else body.topRight(), body.bottomLeft() if rtl else body.bottomRight())
        if i == first:
            p.setPen(QPen(QColor("#6B7280"), 2))
            p.drawLine(body.topRight() if rtl else body.topLeft(), body.bottomRight() if rtl else body.bottomLeft())
        p.setPen(QColor("#111827"))
        p.setFont(font_for(self.font(), self.zoom, True, 9))
        p.drawText(body.adjusted(3, 2, -3, -2), int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), col.name)
        p.setFont(font_for(self.font(), self.zoom, False, 7.5))
        p.setPen(QColor("#6B7280"))
        p.drawText(body.adjusted(3, 2, -3, -3), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                   tr("Man") if col.gender == "M" else tr("Woman"))
        p.restore()


class DayHeader(QHeaderView):
    """The frozen first column: weekday and date; weekend days are tinted."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.zoom = 1.0
        self.setSectionsClickable(False)
        self.setHighlightSections(False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(int(66 * self.zoom), super().sizeHint().height())

    def paintSection(self, p: QPainter, rect: QRect, i: int) -> None:  # noqa: N802
        model: RosterModel = self.model()
        if not isinstance(model, RosterModel) or i >= len(model.days):
            return
        d = model.days[i]
        we = d in model.weekend
        p.save()
        p.setClipRect(rect)
        p.fillRect(rect, QColor("#FBE3A0") if we else QColor("#EEF1F5"))
        p.setPen(QPen(GRID, 1))
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        p.setPen(QPen(QColor("#6B7280"), 2))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        p.drawLine(rect.topLeft() if rtl else rect.topRight(), rect.bottomLeft() if rtl else rect.bottomRight())
        p.setPen(QColor("#111827"))
        p.setFont(font_for(self.font(), self.zoom, True, 9.5))
        p.drawText(rect.adjusted(0, 0, 0, 0), int(Qt.AlignmentFlag.AlignCenter), f"{strf(d, '%a')} {d.day}")
        p.restore()


# ----------------------------------------------------------------------------- coverage strip
class CoverageStrip(QWidget):
    """Under the grid: per reception and day, hours covered / hours needed; red when an hour is uncovered
    (hover shows which hours)."""

    day_clicked = Signal(object)

    LABEL_W = 92
    HEAD_H = 16
    ROW_H = 20

    def __init__(self) -> None:
        super().__init__()
        self.data: dict = {}
        self.days: list[date] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(self.HEAD_H + self.ROW_H)

    def set_data(self, data: dict, days: list[date]) -> None:
        self.data, self.days = data, days
        h = self.HEAD_H + self.ROW_H * max(1, len(data)) + 4
        self.setFixedHeight(h)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, self.HEAD_H + self.ROW_H * max(1, len(self.data)) + 4)

    def _cell_w(self) -> float:
        return max(24.0, (self.width() - self.LABEL_W - 4) / max(1, len(self.days)))

    def _rtl(self) -> bool:
        return i18n.is_rtl()

    def _x(self, di: int, cw: float) -> float:
        """Left edge of the cell of day ``di`` (the first day is at the right in a mirrored layout)."""
        return self.width() - self.LABEL_W - (di + 1) * cw if self._rtl() else self.LABEL_W + di * cw

    def _locate(self, pos: QPoint):
        if not self.days:
            return None
        cw = self._cell_w()
        di = int(((self.width() - self.LABEL_W - pos.x()) if self._rtl() else (pos.x() - self.LABEL_W)) // cw)
        ri = int((pos.y() - self.HEAD_H) // self.ROW_H)
        recs = list(self.data)
        if 0 <= di < len(self.days) and 0 <= ri < len(recs):
            return recs[ri], self.days[di]
        if 0 <= di < len(self.days) and pos.y() < self.HEAD_H:
            return None, self.days[di]
        return None

    def cell_info(self, rec: str, d: date):
        return self.data.get(rec, {}).get(d)

    def tip(self, rec: str, d: date) -> str:
        info = self.cell_info(rec, d)
        if info is None:
            return ""
        cov, need, miss = info
        if need == 0:
            return tr("{rec} – {v0}: no coverage target this day.", rec=rec, v0=strf(d, "%a %d %b"))
        if not miss:
            return tr("{rec} – {v0}: all {need} required hours are covered.", rec=rec, v0=strf(d, "%a %d %b"), need=need)
        return (tr("{rec} – {v0}: {v1} of {need} required hours have nobody on duty:\n", rec=rec, v0=strf(d, "%a %d %b"),
                   v1=len(miss), need=need) + ", ".join(disp_code(m) for m in miss))

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        hit = self._locate(ev.position().toPoint())
        if hit and hit[0]:
            QToolTip.showText(ev.globalPosition().toPoint(), self.tip(*hit), self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        hit = self._locate(ev.position().toPoint())
        if hit:
            self.day_clicked.emit(hit[1])

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        cw = self._cell_w()
        f = font_for(self.font(), 1.0, False, 7.5)
        p.setFont(f)
        for di, d in enumerate(self.days):
            x = self._x(di, cw)
            p.setPen(QColor("#6B7280"))
            p.drawText(QRectF(x, 0, cw, self.HEAD_H), int(Qt.AlignmentFlag.AlignCenter), str(d.day))
        for ri, (rec, per) in enumerate(self.data.items()):
            y = self.HEAD_H + ri * self.ROW_H
            p.setFont(font_for(self.font(), 1.0, True, 8.5))
            p.setPen(QColor("#111827"))
            if self._rtl():
                p.drawText(QRectF(self.width() - self.LABEL_W + 6, y, self.LABEL_W - 6, self.ROW_H), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), rec)
            else:
                p.drawText(QRectF(0, y, self.LABEL_W - 6, self.ROW_H), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), rec)
            p.setFont(f)
            for di, d in enumerate(self.days):
                cov, need, miss = per.get(d, (0, 0, []))
                r = QRectF(self._x(di, cw) + 1, y + 1, cw - 2, self.ROW_H - 2)
                if need == 0:
                    p.fillRect(r, QColor("#E5E7EB"))
                    p.setPen(QColor("#9CA3AF"))
                    txt = "–"
                elif miss:
                    p.fillRect(r, QColor("#DC2626"))
                    p.setPen(QColor("white"))
                    txt = f"{cov}/{need}"
                else:
                    p.fillRect(r, QColor("#C6E0B4"))
                    p.setPen(QColor("#1F2937"))
                    txt = f"{cov}/{need}"
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), txt)
        p.end()


# ----------------------------------------------------------------------------- cell editor
class CellEditDialog(QDialog):
    """Popup to change one cell: shift (the person's allowed shifts) or OFF, reception, overtime, extension.
    A LEAVE cell is shown read-only."""

    def __init__(self, parent, editor, emp_key: str, d: date, leave: bool = False) -> None:
        super().__init__(parent)
        self.editor, self.emp_key, self.d = editor, emp_key, d
        inp = editor.inp
        e = inp.emp_by_key[emp_key]
        self.emp = e
        self.setWindowTitle(tr("Change roster cell"))
        self.setModal(True)
        self.setMinimumWidth(380)
        v = QVBoxLayout(self)
        v.setSpacing(10)
        head = QLabel(f"<b>{e.name}</b> · {strf(d, '%A %d %B %Y')}")
        v.addWidget(head)
        sub = QLabel(tr('Home reception {home}', home=e.home) + (tr(', can also cover {also}', also=e.also) if e.also else ""))
        sub.setProperty("role", "subtitle")
        v.addWidget(sub)
        self.leave = leave
        if leave:
            note = QLabel(tr("This person is on leave on this day. Leave cannot be changed here – change it on the Leave page."))
            note.setWordWrap(True)
            v.addWidget(note)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            bb.rejected.connect(self.reject)
            v.addWidget(bb)
            return
        st = editor.state(emp_key, d)
        cur = st.asg
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.shift = QComboBox()
        self.shift.addItem(tr("OFF – no duty"), None)
        allowed = [s for s in inp.shifts if not e.allowed or s.code.strip().upper() in {a.strip().upper() for a in e.allowed}]
        for s in allowed:
            self.shift.addItem(disp_code(s.code), s.code)
        if cur is not None and self.shift.findData(cur.shift_code) < 0:
            self.shift.addItem(tr('{shift_code}  (not in allowed shifts)', shift_code=cur.shift_code), cur.shift_code)
        self.shift.setCurrentIndex(max(0, self.shift.findData(cur.shift_code)) if cur else 0)
        form.addRow(tr("Shift"), self.shift)
        self.rec = QComboBox()
        self.rec.addItem(tr('{home}  (home)', home=e.home), e.home)
        for r in [x.strip().upper() for x in (e.also or "").split(",") if x.strip()]:
            if r != e.home:
                self.rec.addItem(tr('{r}  (helper)', r=r), r)
        if cur is not None and self.rec.findData(cur.rec) < 0:
            self.rec.addItem(tr('{rec}  (helper)', rec=cur.rec), cur.rec)
        self.rec.setCurrentIndex(max(0, self.rec.findData(cur.rec)) if cur else 0)
        form.addRow(tr("Reception"), self.rec)
        b = editor.roster.blocks.get(emp_key) if editor.roster.blocks else None
        self.block_day = bool(b and b.is_block_day(d, inp.settings.weekday_names))
        self.ot = QCheckBox(tr("Work as overtime") + (tr("  (planned day off – always overtime)") if self.block_day else ""))
        self.ot.setChecked(bool(cur and cur.is_ot) or self.block_day)
        self.ot.setEnabled(not self.block_day)
        form.addRow("", self.ot)
        self.ot_note = QLabel(tr("Overtime on a normal working day is allowed here, but the roster will show it as a warning "
                              "(\"Overtime on a normal working day – manual\") and a rebuild keeps it as you set it."))
        self.ot_note.setWordWrap(True)
        self.ot_note.setProperty("role", "warn")
        self.ot_note.hide()
        form.addRow("", self.ot_note)
        cap = int(max(0, inp.settings.ext_max))
        self.before = QSpinBox()
        self.before.setRange(0, cap)
        self.before.setSuffix(tr(" h"))
        self.before.setValue(cur.ext_before_h if cur else 0)
        self.after = QSpinBox()
        self.after.setRange(0, cap)
        self.after.setSuffix(tr(" h"))
        self.after.setValue(cur.ext_after_h if cur else 0)
        form.addRow(tr("Start earlier by"), self.before)
        form.addRow(tr("Finish later by"), self.after)
        v.addLayout(form)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "hint")
        v.addWidget(self.hint)
        fixed = [f for f in inp.fixed if f.emp.strip().upper() == emp_key and f.date == d]
        if fixed:
            w = QLabel(tr('There is a fixed shift here ({shift}). Your change replaces it in this roster.', shift=fixed[0].shift))
            w.setProperty("role", "warn")
            w.setWordWrap(True)
            v.addWidget(w)
        pin = QLabel(tr("📌 The changed cell is pinned: a rebuild keeps it exactly as you set it."))
        pin.setProperty("role", "hint")
        pin.setWordWrap(True)
        v.addWidget(pin)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_btn = self.buttons.addButton(tr("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_btn.setProperty("primary", True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self.shift.currentIndexChanged.connect(self._sync)
        self.ot.toggled.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        if self.leave:
            return
        code = self.shift.currentData()
        off = code is None
        sh = self.editor.inp.shift_by_key.get((code or "").strip().upper()) if code else None
        split = bool(sh and sh.start2 >= 0)
        self.rec.setEnabled(not off)
        self.ot.setEnabled(not off and not self.block_day)
        for w in (self.before, self.after):
            w.setEnabled(not off and not split and self.emp_cap() > 0)
        if off or split:
            self.before.setValue(0)
            self.after.setValue(0)
        extra_off = off and not self.block_day
        if split:
            txt = tr("A split shift cannot be extended.")
        elif extra_off:
            txt = tr("OFF on a normal working day is an extra day off. It is kept and listed as \"Extra day off (manual)\" – "
                     "not as a problem – but the days around it may be short of people.")
        elif not off:
            txt = tr("Hours before or after the shift count as overtime.")
        else:
            txt = ""
        self.hint.setText(txt)
        self.ot_note.setVisible(not off and not self.block_day and self.ot.isChecked())

    def emp_cap(self) -> int:
        return self.before.maximum()

    def values(self) -> dict:
        """What the user chose: ``shift`` (code or ``None`` = OFF), ``rec``, ``ot``, ``eb``, ``ea``."""
        code = self.shift.currentData()
        off = code is None
        return {"shift": code, "rec": None if off else self.rec.currentData(),
                "ot": (not off) and (self.ot.isChecked() or self.block_day),
                "eb": 0 if off else self.before.value(), "ea": 0 if off else self.after.value()}


__all__ = ["Col", "RosterModel", "RosterDelegate", "GroupHeader", "DayHeader", "CoverageStrip", "CellEditDialog", "LEGEND", "FILL"]
