"""``python -m roster_builder`` launches the Qt app.

Also the entry point of the PyInstaller build, where it runs as a plain script (no package context).
The windowed .exe has no console, so any start-up failure is written to ``RosterBuilder_error.log``
(in the data folder ``%APPDATA%\\RosterBuilder``) and shown in a Windows message box instead of failing silently.

``--smoke-test``: start the whole app on a temporary database, process events, close it and exit 0
(used to check a PyInstaller build; see ``packaging/README_RUN.md``).
"""

import datetime
import os
import sys
import tempfile
import traceback


def _log_path() -> str:
    """Crash log: ``%APPDATA%\\RosterBuilder\\RosterBuilder_error.log`` (else next to the .exe, else %TEMP%)."""
    try:
        try:
            from .runtime import log_path
        except ImportError:  # run as a plain script
            from roster_builder.runtime import log_path
        return str(log_path())
    except Exception:  # noqa: BLE001 - never fail while setting up the crash log
        return os.path.join(tempfile.gettempdir(), "RosterBuilder_error.log")


def _report(text: str) -> None:
    path = _log_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n==== {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  python {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}\n")
            f.write(text)
    except OSError:
        pass
    try:
        sys.stderr.write(text)
    except Exception:  # noqa: BLE001 - no console in the windowed .exe
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text[-1500:] + f"\n\nDetails saved to:\n{path}",
                                             "Roster Builder could not start", 0x10)
        except Exception:  # noqa: BLE001
            pass


def run() -> int:
    fh = None
    try:
        import faulthandler

        fh = open(_log_path(), "a", encoding="utf-8")  # noqa: SIM115 - kept open for hard crashes
        faulthandler.enable(file=fh)
    except Exception:  # noqa: BLE001
        pass
    try:
        try:
            from .ui import run_app, smoke_test
        except ImportError as exc:
            if "relative import" not in str(exc) and "no known parent package" not in str(exc):
                raise
            from roster_builder.ui import run_app, smoke_test  # frozen / run as a script
        if "--smoke-test" in sys.argv[1:]:
            return int(smoke_test() or 0)
        return int(run_app() or 0)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - report every start-up failure
        _report(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(run())
