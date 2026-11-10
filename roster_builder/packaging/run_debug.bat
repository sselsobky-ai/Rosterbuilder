@echo off
REM Starts the app from source with a console and saves everything it prints to run_debug.log.
REM (Crash reports of the normal .exe go to %APPDATA%\RosterBuilder\RosterBuilder_error.log.)
cd /d "%~dp0..\.."
set LOG=roster_builder\packaging\run_debug.log
echo ==== %date% %time% > "%LOG%"
if exist ".venv\Scripts\python.exe" (set PY=.venv\Scripts\python.exe) else (set PY=py -3)
%PY% --version >> "%LOG%" 2>&1
%PY% -c "import PySide6, ortools, openpyxl, reportlab; from PySide6 import QtCore; print('imports ok, PySide6', PySide6.__version__, 'Qt', QtCore.qVersion(), 'ortools', ortools.__version__)" >> "%LOG%" 2>&1
%PY% -X faulthandler -m roster_builder >> "%LOG%" 2>&1
echo exit code %errorlevel% >> "%LOG%"
type "%LOG%"
echo.
echo (log saved to %LOG%)
pause
