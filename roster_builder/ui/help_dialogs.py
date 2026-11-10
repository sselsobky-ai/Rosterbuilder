"""Help menu dialogs: the user guide (USER_GUIDE.md rendered with its screenshots) and About."""

from __future__ import annotations

import sys
from pathlib import Path, PurePath

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                               QVBoxLayout)

from .. import i18n, runtime
from ..i18n import tr
from ..store import default_db_path

GUIDE_CSS = """
body { font-size: 10pt; }
h1 { font-size: 20pt; color: #1F3864; margin-bottom: 4px; }
h2 { font-size: 15pt; color: #1F3864; margin-top: 18px; }
h3 { font-size: 12pt; color: #374151; }
code { background: #F3F4F6; font-family: Consolas, 'DejaVu Sans Mono', monospace; }
table { border-collapse: collapse; }
th { background: #E5E7EB; }
a { color: #2563EB; }
"""
IMAGE_MAX_WIDTH = 840


def open_folder(path: PurePath | str) -> bool:
    """Show a folder in the file manager (Explorer on Windows)."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


class UserGuideDialog(QDialog):
    """The manager's guide, rendered from ``USER_GUIDE.md`` (screenshots included, scaled to fit)."""

    def __init__(self, parent=None, path: Path | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path) if path else runtime.user_guide_path()
        self.setWindowTitle(tr("User guide"))
        self.resize(1000, 700)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._link)
        self.browser.document().setDefaultStyleSheet(GUIDE_CSS)
        self.browser.setStyleSheet("QTextBrowser { background: white; border: 1px solid #CBD2DC; padding: 8px; }")
        v.addWidget(self.browser, 1)
        row = QHBoxLayout()
        self.path_label = QLabel(str(self.path))
        self.path_label.setProperty("role", "hint")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.path_label, 1)
        close = QPushButton(tr("Close"))
        close.setProperty("primary", True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self._load()

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            text = tr("# User guide\n\nThe user guide file could not be opened.\n\n`{path}`\n\n{exc}", path=str(self.path), exc=str(exc))
        folder = self.path.parent
        self.browser.setSearchPaths([str(folder)])
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(folder) + "/"))
        self.browser.setMarkdown(text)
        if self.path.name.endswith("_AR.md") or i18n.is_rtl():
            self._make_rtl()
        self._fit_images(folder)

    def _make_rtl(self) -> None:
        """The Arabic guide: every paragraph, list and table flows right to left."""
        self.browser.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        doc = self.browser.document()
        opt = doc.defaultTextOption()
        opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
        doc.setDefaultTextOption(opt)
        cur = QTextCursor(doc)
        cur.select(QTextCursor.SelectionType.Document)
        fmt = QTextBlockFormat()
        fmt.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        fmt.setAlignment(Qt.AlignmentFlag.AlignRight)
        cur.mergeBlockFormat(fmt)

    def _fit_images(self, folder: Path) -> None:
        """Scale wide screenshots down to ``IMAGE_MAX_WIDTH`` pixels (Qt shows markdown images at full size)."""
        doc = self.browser.document()
        todo = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if fmt.isImageFormat():
                    todo.append((frag.position(), frag.length(), fmt.toImageFormat()))
                it += 1
            block = block.next()
        for pos, length, img in todo:
            src = Path(img.name())
            if not src.is_absolute():
                src = (folder / src).resolve()
            qi = QImage(str(src))
            if qi.isNull():
                continue
            w = min(qi.width(), IMAGE_MAX_WIDTH)
            img.setWidth(w)
            img.setHeight(qi.height() * w / qi.width())
            cur = QTextCursor(doc)
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            cur.setCharFormat(QTextCharFormat(img))

    def _link(self, url: QUrl) -> None:
        if url.isRelative() or url.scheme() == "":
            frag = url.fragment()
            if frag:
                self.browser.scrollToAnchor(frag)
            return
        QDesktopServices.openUrl(url)


class AboutDialog(QDialog):
    """Version, where the data and the log file are, with buttons to open the folders."""

    def __init__(self, parent=None, db_path: Path | str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("About Roster Builder"))
        self.setMinimumWidth(720)
        if db_path and str(db_path) != ":memory:":
            self.db_path = db_path if isinstance(db_path, PurePath) else Path(db_path)
        else:
            self.db_path = default_db_path()
        self.data_folder = self.db_path.parent
        self.log_file = runtime.log_path()
        log_dir = self.log_file.parent
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        title = QLabel(tr("Roster Builder"))
        title.setProperty("role", "title")
        v.addWidget(title)
        sub = QLabel(tr('Version {__version__}', __version__=runtime.__version__))
        sub.setProperty("role", "subtitle")
        v.addWidget(sub)
        blurb = QLabel(tr("Builds the monthly duty roster for your reception teams. Everything you enter is saved "
                       "automatically on this computer; nothing is sent anywhere."))
        blurb.setWordWrap(True)
        v.addWidget(blurb)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.data_label = self._path_label(str(self.data_folder))
        self.db_label = self._path_label(str(self.db_path))
        self.log_label = self._path_label(str(self.log_file))
        form.addRow(tr("Data folder"), self.data_label)
        form.addRow(tr("Database"), self.db_label)
        form.addRow(tr("Backups"), self._path_label(str(self.data_folder / "backups")))
        form.addRow(tr("Log file"), self.log_label)
        try:
            import PySide6

            qt_ver = PySide6.__version__
        except Exception:  # noqa: BLE001
            qt_ver = "?"
        form.addRow(tr("Built with"), QLabel(tr('Python {v0}  ·  PySide6 {qt_ver}', v0=sys.version.split()[0], qt_ver=qt_ver)))
        v.addLayout(form)
        note = QLabel(tr("If something goes wrong, the log file tells what happened. Send it to whoever supports you."))
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        v.addWidget(note)
        row = QHBoxLayout()
        self.open_data_btn = QPushButton(tr("Open data folder"))
        self.open_data_btn.setToolTip(tr("Show the folder with your database and backups in Explorer."))
        self.open_data_btn.clicked.connect(lambda: open_folder(self.data_folder))
        self.open_log_btn = QPushButton(tr("Open log folder"))
        self.open_log_btn.clicked.connect(lambda: open_folder(log_dir))
        self.copy_btn = QPushButton(tr("Copy paths"))
        self.copy_btn.clicked.connect(self.copy_paths)
        close = QPushButton(tr("Close"))
        close.setProperty("primary", True)
        close.clicked.connect(self.accept)
        row.addWidget(self.open_data_btn)
        row.addWidget(self.open_log_btn)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)

    @staticmethod
    def _path_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(False)  # a path is one line; the dialog is wide enough
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lab

    def copy_paths(self) -> None:
        QApplication.clipboard().setText(tr('Data folder: {data_folder}\nDatabase: {db_path}\nLog file: {log_file}\nVersion: {__version__}', data_folder=self.data_folder, db_path=self.db_path, log_file=self.log_file, __version__=runtime.__version__))
