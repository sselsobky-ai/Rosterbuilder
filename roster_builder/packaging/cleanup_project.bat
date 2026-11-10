@echo off
REM Roster Builder - clean-up of the project folder (leftover developer files, test files, caches, old build output).
REM Run it from anywhere: it goes to the project folder (two levels above this script), SHOWS what it would delete,
REM and deletes only after you type YES.  It never touches .venv, the program code (roster_builder\*.py, roster_builder\ui,
REM roster_builder\packaging), index-with-projects.html, or anything outside the project folder.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0..\.."
set "PROJ=%CD%"
if not exist "roster_builder\__main__.py" (
    echo This does not look like the Roster Builder project folder: %PROJ%
    echo Nothing was deleted.
    pause
    exit /b 1
)
echo Project folder: %PROJ%
echo.

REM screenshots the user guides show (everything else in roster_builder\screens is deleted)
set "KEEP=|10_coverage.png|10_employees.png|11A_ot_priority_list.png|11_input_check.png|11_leave.png|11_new_month_dialog.png|11_welcome.png|12_roster.png|12_roster_edit_popup.png|12_roster_problem.png|13_about_dialog.png|13_build_page.png|13_roster_manual_ot_warning.png|14_ar_checks.png|14_ar_employees.png|14_ar_roster.png|14_ar_welcome.png|"

set "LIST=%TEMP%\rb_cleanup_list.txt"
if exist "%LIST%" del "%LIST%"
set /a N=0

REM ---- single files
for %%F in (
  "roster_builder\BUILD_GUIDE.md"
  "roster_builder\REVIEW.md"
  "roster_builder\VERDICT.txt"
  "roster_builder\checkpoint_workflow.py"
  "roster_builder\checkpoint_dashboard.html"
  "roster_builder\checkpoint-board.html"
  "roster_builder\checkpoint_bindings.json"
  "roster_builder\checkpoint_dispatch.sqlite3"
  "roster_builder\checkpoint.pause"
  "roster_builder\RosterBuilder_current_source.bas"
  "roster_builder\gui.py"
  "roster_builder\cli.py"
  "roster_builder\packaging\run_debug.log"
  "RosterBuilder_error.log"
  "dist\RosterBuilder_error.log"
) do (
  if exist %%F (echo F^|%%~F>>"%LIST%" & set /a N+=1)
)

REM ---- screenshots that no guide shows
REM (the keep-list is written to a file: a pipe inside a FOR block would lose !KEEP!)
set "KEEPF=%TEMP%\rb_cleanup_keep.txt"
>"%KEEPF%" echo !KEEP!
if exist "roster_builder\screens\" (
  for %%F in ("roster_builder\screens\*") do (
    findstr /c:"|%%~nxF|" "%KEEPF%" >nul
    if errorlevel 1 (echo F^|%%~F>>"%LIST%" & set /a N+=1)
  )
)
del "%KEEPF%" 2>nul

REM ---- folders (.venv is never visited)
REM (dist\ is kept: build_exe.bat overwrites it and it holds the installer you build)
for %%D in ("tests" "build" ".pytest_cache" "roster_builder\.pytest_cache" "sample_out") do (
  if exist "%%~D\" (echo D^|%%~D>>"%LIST%" & set /a N+=1)
)
for /d /r "roster_builder" %%D in (__pycache__) do (
  if exist "%%D\" (echo D^|%%D>>"%LIST%" & set /a N+=1)
)
if exist "tests\" (
  for /d /r "tests" %%D in (__pycache__) do (
    if exist "%%D\" (echo D^|%%D>>"%LIST%" & set /a N+=1)
  )
)

if %N%==0 (
  echo Nothing to delete - the project folder is already clean.
  pause
  exit /b 0
)

echo These %N% items will be deleted ^(F = file, D = folder with everything inside^):
echo.
for /f "usebackq tokens=1,* delims=|" %%a in ("%LIST%") do echo   %%a  %%b
echo.
echo NOT touched: .venv, the program code, index-with-projects.html and anything outside %PROJ%.
echo Your roster data (%%APPDATA%%\RosterBuilder) is not in the project folder and is never touched.
echo.
set "ANS="
set /p "ANS=Type YES to delete these items (anything else cancels): "
if not "%ANS%"=="YES" (
    echo Cancelled. Nothing was deleted.
    del "%LIST%" 2>nul
    pause
    exit /b 0
)

echo.
set /a DONE=0
set /a FAILED=0
for /f "usebackq tokens=1,* delims=|" %%a in ("%LIST%") do (
  if "%%a"=="F" (
    del /f /q "%%b" >nul 2>&1
    if exist "%%b" (echo   FAILED  %%b & set /a FAILED+=1) else (echo   deleted %%b & set /a DONE+=1)
  ) else (
    rmdir /s /q "%%b" >nul 2>&1
    if exist "%%b" (echo   FAILED  %%b & set /a FAILED+=1) else (echo   deleted %%b & set /a DONE+=1)
  )
)
del "%LIST%" 2>nul
echo.
echo Done: !DONE! deleted, !FAILED! could not be deleted ^(close any program that uses them and run this again^).
pause
exit /b 0
