"""First-run pieces: the welcome page, the import report dialog and the setup-order strip."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import count_phrase, iso, month_name, tr
from ..importer import SECTIONS, ImportReport


class WelcomePage(QWidget):
    """Shown while the app holds no data at all: two big choices."""

    import_clicked = Signal()
    empty_clicked = Signal()
    language_clicked = Signal(str)  # "en" / "ar"

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.key = "welcome"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 24, 40, 24)
        top = QHBoxLayout()
        top.addStretch(1)
        other = "en" if i18n.is_ar() else "ar"
        self.lang_btn = QPushButton("English" if other == "en" else "العربية")
        self.lang_btn.setToolTip(tr("Language"))
        self.lang_btn.setProperty("flat-link", True)
        self.lang_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lang_btn.clicked.connect(lambda: self.language_clicked.emit(other))
        top.addWidget(self.lang_btn)
        outer.addLayout(top)
        outer.addStretch(2)
        title = QLabel(tr("Welcome to Roster Builder"))
        title.setProperty("role", "big")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(tr("Plan your 24/7 reception roster without a spreadsheet. Your staff, shifts and coverage are entered once; "
                     "each new month starts from them."))
        sub.setProperty("role", "subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        outer.addWidget(title)
        outer.addSpacing(8)
        outer.addWidget(sub)
        outer.addSpacing(28)
        row = QHBoxLayout()
        row.setSpacing(28)
        row.addStretch(1)
        self.import_btn = QPushButton(tr("Import from my Excel roster…"))
        self.import_btn.setProperty("big", True)
        self.import_btn.setProperty("primary", True)
        self.import_btn.setToolTip(tr("Pick your old roster workbook (.xlsx or .xlsm). The app reads it once to fill in its screens."))
        self.import_btn.clicked.connect(self.import_clicked.emit)
        self.empty_btn = QPushButton(tr("Start empty"))
        self.empty_btn.setProperty("big", True)
        self.empty_btn.setToolTip(tr("Begin with nothing and enter your setup step by step."))
        self.empty_btn.clicked.connect(self.empty_clicked.emit)
        row.addLayout(self._card(self.import_btn, tr("Best if you already have a roster workbook. Staff, shifts, coverage, leave and "
                                                  "fixed shifts are filled in for you. Your Excel file is never changed.")))
        row.addLayout(self._card(self.empty_btn, tr("Best for a fresh start. You get a short checklist: Settings, Receptions, "
                                                 "Shifts, Coverage, Employees, then the month.")))
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)

    @staticmethod
    def _card(btn: QPushButton, text: str) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(10)
        col.addWidget(btn)
        lab = QLabel(text)
        lab.setProperty("role", "hint")
        lab.setWordWrap(True)
        lab.setFixedWidth(340)
        lab.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        col.addWidget(lab, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(1)
        return col


class ImportReportDialog(QDialog):
    """What an import did: counts, skipped rows, warnings and the input check of the new month."""

    def __init__(self, parent, report: ImportReport) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle(tr("Import finished"))
        self.setMinimumSize(720, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        name = iso(Path(report.source).name) or tr("the workbook")
        head = QLabel((tr("✓  Replaced your data with ") if report.mode == "replace" else tr("✓  Imported ")) + name)
        head.setProperty("role", "ok")
        head.setStyleSheet("font-size: 14pt; font-weight: 600;")
        head.setWordWrap(True)
        lay.addWidget(head)
        if report.year:
            sub = QLabel(tr('{v0} {year} was created as a draft month.', v0=month_name(report.month), year=report.year))
            sub.setProperty("role", "subtitle")
            lay.addWidget(sub)
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(10)
        tiles.setVerticalSpacing(10)
        shown = [(k, label) for k, label in SECTIONS if k in report.counts]
        for i, (k, label) in enumerate(shown):
            tile = QFrame()
            tile.setObjectName("card")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(12, 8, 12, 8)
            tl.setSpacing(0)
            n = QLabel(str(report.counts[k]))
            n.setProperty("role", "count")
            t = QLabel(tr(label))
            t.setProperty("role", "hint")
            tl.addWidget(n)
            tl.addWidget(t)
            tiles.addWidget(tile, i // 4, i % 4)
        lay.addLayout(tiles)
        n_skip, n_warn, n_find = len(report.skipped), len(report.warnings), len(report.findings)
        line = QLabel(f"{count_phrase(n_skip, 'skipped row')}  ·  {count_phrase(n_warn, 'warning')}  ·  "
                      f"{count_phrase(n_find, 'input-check finding')}")
        line.setProperty("role", "subtitle")
        lay.addWidget(line)
        self.details = QPlainTextEdit(report.summary_text())
        self.details.setReadOnly(True)
        self.details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        lay.addWidget(self.details, 1)
        btns = QHBoxLayout()
        self.copy_btn = QPushButton(tr("Copy report"))
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        self.close_btn = QPushButton(tr("Continue"))
        self.close_btn.setProperty("primary", True)
        self.close_btn.clicked.connect(self.accept)
        btns.addWidget(self.copy_btn)
        btns.addStretch(1)
        btns.addWidget(self.close_btn)
        lay.addLayout(btns)


class SetupGuide(QFrame):
    """A one-line checklist of the setup order, shown after "Start empty"."""

    go = Signal(str)
    dismissed = Signal()

    STEPS = [("settings", "Settings"), ("receptions", "Receptions"), ("shifts", "Shifts"), ("coverage", "Coverage"),
             ("employees", "Employees"), ("month", "Month")]

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("guide")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 6, 10, 6)
        h.setSpacing(2)
        lead = QLabel(tr("Setup order:"))
        lead.setStyleSheet("font-weight: 600;")
        h.addWidget(lead)
        self.buttons: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate(self.STEPS):
            if i:
                arrow = QLabel("→")
                arrow.setProperty("role", "hint")
                h.addWidget(arrow)
            b = QPushButton(f"{i + 1}. {tr(text)}")
            b.setProperty("flat-link", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.go.emit(k))
            h.addWidget(b)
            self.buttons[key] = b
        h.addStretch(1)
        self.hide_btn = QPushButton(tr("Hide"))
        self.hide_btn.setProperty("flat-link", True)
        self.hide_btn.setToolTip(tr("Hide this checklist."))
        self.hide_btn.clicked.connect(self.dismissed.emit)
        h.addWidget(self.hide_btn)

    def update_state(self, done: dict[str, bool], current: str) -> None:
        for i, (key, text) in enumerate(self.STEPS):
            b = self.buttons[key]
            mark = "✓ " if done.get(key) else f"{i + 1}. "
            b.setText(mark + tr(text))
            b.setStyleSheet("text-decoration: underline;" if key == current else "")
            b.setToolTip(tr("Done") if done.get(key) else tr("Still to do"))
