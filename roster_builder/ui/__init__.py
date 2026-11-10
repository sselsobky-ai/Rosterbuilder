"""PySide6 user interface of the roster app (BUILD_GUIDE section 8).  Qt is imported lazily."""

from __future__ import annotations


def _set_icon(app) -> None:
    """Window / task-bar icon from the bundled ``packaging/app.png`` (silently skipped when it is missing)."""
    try:
        from PySide6.QtGui import QIcon

        from .. import runtime

        p = runtime.bundle_root() / "roster_builder" / "packaging" / "app.png"
        if p.exists():
            app.setWindowIcon(QIcon(str(p)))
    except Exception:  # noqa: BLE001 - cosmetic
        pass


def run_app(db_path=None) -> int:
    """Start the Qt app on the database at ``db_path`` (default: the user's app-data folder). Returns the exit code."""
    import sys

    from PySide6.QtWidgets import QApplication

    from .. import i18n
    from ..store import Store, default_db_path
    from .main_window import MainWindow
    from .theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Roster Builder")
    app.setOrganizationName("RosterBuilder")
    apply_theme(app)
    i18n.load_language()  # the saved language ("en" / "ar"; the ROSTER_LANG environment variable overrides it)
    i18n.apply_language(app)
    _set_icon(app)
    path = db_path or default_db_path()
    store = Store(path)
    try:  # rotating backup on every start (BUILD_GUIDE section 5)
        if str(path) != ":memory:" and store.has_master_data():
            store.backup()
    except Exception:  # noqa: BLE001 - a failed backup must not stop the app
        pass
    win = MainWindow(store)
    win.show()
    app.main_window = win  # a language switch replaces the window; this keeps the current one alive
    code = app.exec()
    store.close()
    return int(code)


def smoke_test() -> int:
    """``RosterBuilder.exe --smoke-test``: open the main window on a temporary database, open the Help dialogs, check that
    the solver / export libraries load, process events, close.  Returns 0 when everything worked, raises otherwise."""
    import sys
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from .. import i18n
    from ..store import Store
    from .help_dialogs import AboutDialog, UserGuideDialog
    from .main_window import MainWindow
    from .theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Roster Builder")
    apply_theme(app)
    import os

    i18n.set_language((os.environ.get("ROSTER_LANG") or "en").strip().lower())  # ROSTER_LANG=ar checks the Arabic build
    i18n.apply_language(app)
    _set_icon(app)
    with tempfile.TemporaryDirectory() as tmp:
        from PySide6.QtCore import QSettings

        store = Store(Path(tmp) / "roster.db")
        win = MainWindow(store, QSettings(str(Path(tmp) / "ui.ini"), QSettings.Format.IniFormat))
        win.show()
        app.processEvents()
        guide = UserGuideDialog(win)
        if len(guide.browser.toPlainText()) < 500:
            raise RuntimeError(f"user guide is missing or empty: {guide.path}")
        about = AboutDialog(win)
        app.processEvents()
        about.close()
        guide.close()
        # every engine module loads (frozen builds miss modules that are only imported lazily)
        import importlib
        import pkgutil

        import roster_builder

        for mod in pkgutil.iter_modules(roster_builder.__path__):
            importlib.import_module(f"roster_builder.{mod.name}")
        # engine libraries: a tiny CP-SAT model, an Excel workbook and a PDF in memory
        from ortools.sat.python import cp_model

        m = cp_model.CpModel()
        x = m.NewBoolVar("x")
        m.Add(x == 1)
        if cp_model.CpSolver().Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError("OR-tools could not solve a trivial model")
        import io

        import openpyxl
        from reportlab.platypus import Paragraph, SimpleDocTemplate
        from reportlab.lib.styles import getSampleStyleSheet

        openpyxl.Workbook().save(io.BytesIO())
        SimpleDocTemplate(io.BytesIO()).build([Paragraph("ok", getSampleStyleSheet()["Normal"])])
        win.close()
        app.processEvents()
        store.close()
    print("smoke test ok")
    return 0
