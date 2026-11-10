"""Where things live at run time (standard library only, so the crash handler in ``__main__`` can use it).

* the **data folder** (database, backups) - ``%APPDATA%\\RosterBuilder`` on Windows,
* the **log file** ``RosterBuilder_error.log`` (crash reports) - in the data folder,
* the bundled **user guide** and its screenshots (next to the code, also inside the PyInstaller bundle).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "Roster Builder"
__version__ = "1.0.0"
LOG_NAME = "RosterBuilder_error.log"


def data_dir() -> Path:
    """Folder of the database and its backups (the same as the database's folder)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "RosterBuilder"
    return Path.home() / ".rosterbuilder"


def log_candidates() -> list[Path]:
    """Folders tried for the crash log, best first: data folder, next to the .exe, the temp folder."""
    out = [data_dir()]
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).parent)
    out.append(Path(tempfile.gettempdir()))
    return out


def log_path() -> Path:
    """The crash log file (created if needed); falls back to the next folder when one is not writable."""
    for d in log_candidates():
        p = d / LOG_NAME
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8"):
                pass
            return p
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / LOG_NAME


def bundle_root() -> Path:
    """The folder that contains ``roster_builder/`` (the project folder, or the PyInstaller bundle folder)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


def user_guide_path(lang: str | None = None) -> Path:
    """``USER_GUIDE.md`` (English) or ``USER_GUIDE_AR.md`` (Arabic; ``lang`` defaults to the app language).  The screenshots are
    at ``../screens/`` relative to it, in the source tree and in the bundle."""
    if lang is None:
        from . import i18n

        lang = i18n.lang()
    name = "USER_GUIDE_AR.md" if lang == "ar" else "USER_GUIDE.md"
    return bundle_root() / "roster_builder" / "packaging" / name
