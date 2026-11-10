"""The one light theme of the app (Segoe UI 10, 8-px spacing, one accent colour)."""

from __future__ import annotations

import re

ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_SOFT = "#DBEAFE"
BG = "#F3F4F6"
SURFACE = "#FFFFFF"
BORDER = "#CBD2DC"
TEXT = "#1F2937"
MUTED = "#6B7280"
ERROR = "#DC2626"
ERROR_SOFT = "#FEF2F2"
WARN = "#D97706"
WARN_SOFT = "#FFFBEB"
OK = "#15803D"
ROW_ALT = "#F8FAFC"

FONT_FAMILIES = ["Segoe UI", "Noto Sans", "Inter", "DejaVu Sans", "Arial", "sans-serif"]
SPACING = 8
ROW_HEIGHT = 44


_ARROWS: dict[str, str] = {}


def _arrows() -> dict[str, str]:
    """Small PNG arrows for combo boxes and spin boxes (drawn once into the temp folder)."""
    if _ARROWS:
        return _ARROWS
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap, QPolygonF

    if QGuiApplication.instance() is None:
        return {}
    folder = Path(tempfile.gettempdir()) / "roster_builder_theme"
    folder.mkdir(exist_ok=True)
    for name, pts in (("down", [(1.5, 3.5), (8.5, 3.5), (5, 7.5)]), ("up", [(1.5, 6.5), (8.5, 6.5), (5, 2.5)])):
        pm = QPixmap(10, 10)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4B5563"))
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.end()
        path = folder / f"arrow_{name}.png"
        pm.save(str(path))
        _ARROWS[name] = path.as_posix()
    return _ARROWS


def _checks() -> dict[str, str]:
    """PNG check boxes (normal / checked / disabled / disabled checked) so a disabled box really looks disabled."""
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap

    if QGuiApplication.instance() is None:
        return {}
    folder = Path(tempfile.gettempdir()) / "roster_builder_theme"
    folder.mkdir(exist_ok=True)
    out: dict[str, str] = {}
    for name, fill, edge, tick in (("off", SURFACE, "#94A3B8", None), ("on", ACCENT, ACCENT, "white"),
                                   ("off_dis", "#F3F4F6", "#D1D5DB", None), ("on_dis", "#D1D5DB", "#D1D5DB", "white")):
        pm = QPixmap(36, 36)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(edge), 2))
        p.setBrush(QColor(fill))
        p.drawRoundedRect(QRectF(2, 2, 32, 32), 7, 7)
        if tick:
            pen = QPen(QColor(tick), 4.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(9, 19), QPointF(15, 25), QPointF(27, 11)])
        p.end()
        path = folder / f"check_{name}.png"
        pm.save(str(path))
        out[name] = path.as_posix()
    return out


def _check_css() -> str:
    c = _checks()
    if not c:
        return ""
    return f"""
QCheckBox::indicator {{ width: 18px; height: 18px; image: url({c['off']}); }}
QCheckBox::indicator:checked {{ image: url({c['on']}); }}
QCheckBox::indicator:disabled {{ image: url({c['off_dis']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({c['on_dis']}); }}
QListView::indicator {{ width: 18px; height: 18px; image: url({c['off']}); }}
QListView::indicator:checked {{ image: url({c['on']}); }}
QListView::indicator:disabled {{ image: url({c['off_dis']}); }}
QListView::indicator:checked:disabled {{ image: url({c['on_dis']}); }}
"""


def _arrow_css() -> str:
    a = _arrows()
    if not a:
        return ""
    return f"""
QComboBox::drop-down, QDateEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({a['down']}); width: 10px; height: 10px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 20px;
    border: none; border-left: 1px solid {BORDER}; background: #F3F4F6; border-top-right-radius: 5px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 20px;
    border: none; border-left: 1px solid {BORDER}; border-top: 1px solid {BORDER}; background: #F3F4F6; border-bottom-right-radius: 5px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {ACCENT_SOFT}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({a['up']}); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({a['down']}); width: 8px; height: 8px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 24px; }}
QDateEdit {{ padding-right: 28px; }}
QSpinBox:focus, QDoubleSpinBox:focus, QSpinBox[state], QDoubleSpinBox[state] {{ padding-right: 23px; }}
"""


def _mirror(css: str) -> str:
    """The style sheet for a right-to-left layout: every physical left / right of a rule is swapped (borders, paddings,
    radii, sub-control positions) so spin buttons, drop-downs, the sidebar accent and segmented buttons mirror."""
    def sw(m):
        return "right" if m.group(0) == "left" else "left"

    css = re.sub(r"(?<=-)(left|right)(?=[-:])", sw, css)
    css = re.sub(r"(subcontrol-position:[^;]*)", lambda m: re.sub(r"left|right", sw, m.group(1)), css)
    css = re.sub(r"(?<![-\w])(left|right):", lambda m: sw(m) + ":", css)
    return css.replace("padding: 6px 28px 6px 20px", "padding: 6px 20px 6px 28px")


def stylesheet(rtl: bool | None = None) -> str:
    """The application style sheet (``rtl`` defaults to the current language's direction)."""
    from .. import i18n

    css = _stylesheet(i18n.font_families())
    return _mirror(css) if (i18n.is_rtl() if rtl is None else rtl) else css


def _stylesheet(families) -> str:
    fam = ", ".join(f'"{f}"' if " " in f else f for f in families)
    return f"""
* {{ font-family: {fam}; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page, QWidget#pageBody, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}

QLabel[role="title"] {{ font-size: 16pt; font-weight: 600; }}
QLabel[role="subtitle"], QLabel[role="hint"], QLabel[role="unit"] {{ color: {MUTED}; }}
QLabel[role="hint"] {{ font-size: 9pt; }}
QLabel[role="error"] {{ color: {ERROR}; font-size: 9pt; }}
QLabel[role="warn"] {{ color: {WARN}; font-size: 9pt; }}
QLabel[role="ok"] {{ color: {OK}; }}
QLabel[role="msg"] {{ color: {ERROR}; background: {ERROR_SOFT}; border: 1px solid {ERROR}; border-radius: 6px; padding: 6px 10px; }}

QFrame#card, QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QGroupBox {{ margin-top: 16px; padding: 16px 12px 10px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; background: {BG}; color: {TEXT}; }}

QFrame#banner {{ background: {WARN_SOFT}; border: 1px solid {WARN}; border-radius: 6px; }}
QFrame#banner QLabel {{ color: {TEXT}; }}
QFrame#monthbar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel#monthLabel {{ font-size: 13pt; font-weight: 600; }}
QLabel#statusChip {{ border-radius: 9px; padding: 2px 10px; background: {ACCENT_SOFT}; color: {ACCENT_DARK}; font-size: 9pt; font-weight: 600; }}
QLabel#statusChip[built="true"] {{ background: #E5E7EB; color: {TEXT}; }}

QListWidget#sidebar {{ background: {SURFACE}; border: none; border-right: 1px solid {BORDER}; padding: 8px 0; outline: 0; }}
QListWidget#sidebar::item {{ padding: 10px 20px; border-left: 3px solid transparent; }}
QListWidget#sidebar::item:hover {{ background: {BG}; }}
QListWidget#sidebar::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; border-left: 3px solid {ACCENT}; font-weight: 600; }}
QListWidget#sidebar::item:disabled {{ color: {MUTED}; font-size: 8pt; padding: 14px 20px 4px 20px; }}

QPushButton {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px; min-height: 20px; }}
QPushButton:hover {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #BFDBFE; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; padding: 5px 13px; }}
QPushButton:disabled {{ color: #9CA3AF; background: #F3F4F6; border-color: #E5E7EB; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[primary="true"]:disabled {{ background: #93C5FD; border-color: #93C5FD; color: white; }}
QPushButton[danger="true"]:hover {{ background: {ERROR_SOFT}; border-color: {ERROR}; color: {ERROR}; }}
QPushButton[chip="true"] {{ border-radius: 14px; padding: 4px 0px; min-width: 42px; min-height: 18px; font-size: 9pt; background: {SURFACE}; }}
QPushButton[chip="true"]:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[chip="true"]:checked:hover {{ background: {ACCENT_DARK}; }}
QPushButton[chip="true"]:disabled:checked {{ background: #93C5FD; border-color: #93C5FD; color: white; }}
QPushButton[alert="true"] {{ border: 1px solid {ERROR}; color: {ERROR}; font-weight: 600; background: {ERROR_SOFT}; }}
QPushButton[alert="true"]:hover {{ background: #FEE2E2; }}
QPushButton[seg="left"], QPushButton[seg="right"] {{ border-radius: 0px; padding: 6px 18px; }}
QPushButton[seg="left"] {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QPushButton[seg="right"] {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: none; }}
QPushButton[seg]:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[navbtn="true"] {{ padding: 4px 10px; min-width: 18px; }}

QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px; min-height: 22px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QDateEdit:hover {{ border-color: #94A3B8; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus {{ border: 2px solid {ACCENT}; padding: 3px 7px; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QDateEdit:disabled {{ background: #F3F4F6; color: #9CA3AF; }}
QLineEdit[state="error"], QPlainTextEdit[state="error"], QSpinBox[state="error"], QDoubleSpinBox[state="error"], QComboBox[state="error"],
QDateEdit[state="error"], QFrame[state="error"], QListWidget[state="error"] {{ border: 2px solid {ERROR}; background: {ERROR_SOFT}; padding: 3px 7px; }}
QLineEdit[state="warn"], QSpinBox[state="warn"], QDoubleSpinBox[state="warn"], QComboBox[state="warn"], QDateEdit[state="warn"], QFrame[state="warn"] {{ border: 2px solid {WARN}; background: {WARN_SOFT}; padding: 3px 7px; }}
QFrame[chips="true"] {{ border: 2px solid transparent; border-radius: 8px; background: transparent; padding: 0; }}
QFrame[chips="true"][state="error"] {{ border: 2px solid {ERROR}; background: {ERROR_SOFT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: 0; }}

QCheckBox {{ spacing: 8px; min-height: 22px; }}
QCheckBox:focus {{ color: {ACCENT_DARK}; }}
QCheckBox:disabled {{ color: #9CA3AF; }}
QListWidget:disabled {{ background: #F3F4F6; color: #9CA3AF; }}
QListWidget::item:disabled {{ color: #9CA3AF; }}
QLabel:disabled {{ color: #9CA3AF; }}
QCheckBox[state="error"] {{ color: {ERROR}; }}
QCheckBox[state="warn"] {{ color: {WARN}; }}

QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; outline: 0; }}
QListWidget::item {{ padding: 6px 10px; border-bottom: 1px solid #EEF0F3; }}
QListWidget::item:hover {{ background: {ROW_ALT}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

QTableWidget {{ background: {SURFACE}; alternate-background-color: {ROW_ALT}; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: transparent; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: 0; }}
QTableWidget::item {{ padding: 4px 8px; }}
QTableWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QHeaderView::section {{ background: #EEF1F5; border: none; border-bottom: 1px solid {BORDER}; padding: 8px 10px; font-weight: 600; color: {TEXT}; }}
QTableCornerButton::section {{ background: #EEF1F5; border: none; }}

QFrame#finding {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#finding:hover {{ border-color: {ACCENT}; background: #F8FBFF; }}
QLabel[role="group"] {{ font-weight: 600; font-size: 11pt; }}
QLabel[role="badge"] {{ background: {ERROR}; color: white; border-radius: 9px; padding: 0px 7px; font-size: 8pt; font-weight: 700; }}
QLabel[role="badge-ok"] {{ background: {OK}; color: white; border-radius: 9px; padding: 0px 7px; font-size: 8pt; font-weight: 700; }}
QLabel[role="big"] {{ font-size: 26pt; font-weight: 600; }}
QLabel[role="count"] {{ font-size: 22pt; font-weight: 600; color: {ACCENT_DARK}; }}
QFrame#guide {{ background: {ACCENT_SOFT}; border: 1px solid #93C5FD; border-radius: 6px; }}
QFrame#guide QLabel {{ color: {TEXT}; }}
QPushButton[flat-link="true"] {{ background: transparent; border: none; color: {ACCENT_DARK}; padding: 2px 6px; font-weight: 600; }}
QPushButton[flat-link="true"]:hover {{ background: white; border-radius: 4px; }}
QPushButton[big="true"] {{ font-size: 13pt; padding: 22px 32px; min-width: 330px; border-radius: 10px; }}
QPushButton[big="true"][primary="true"] {{ padding: 22px 32px; }}
QMenuBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 6px 12px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::item:disabled {{ color: #9CA3AF; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
{_arrow_css()}
{_check_css()}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #CBD5E1; border-radius: 5px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #94A3B8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #CBD5E1; border-radius: 5px; min-width: 30px; margin: 2px; }}
"""


def apply_theme(app) -> None:
    """Fusion style + the stylesheet + the default font on a ``QApplication``."""
    from PySide6.QtGui import QFont

    from .. import i18n

    app.setStyle("Fusion")
    f = QFont()
    f.setFamilies(i18n.font_families())
    f.setPointSize(10)
    app.setFont(f)
    app.setStyleSheet(stylesheet())
