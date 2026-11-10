@echo off
REM Builds dist\RosterBuilder\RosterBuilder.exe and the installer dist\installer\RosterBuilder_Setup_<version>.exe
REM (Windows; Python 3.11+, tested with 3.14 from python.org).
REM Double-click it, or run it from any folder. The window stays open at the end so you can read the result.
setlocal EnableDelayedExpansion
cd /d "%~dp0..\.."
echo Project folder: %CD%

if not exist .venv\Scripts\python.exe (
    echo Creating the virtual environment .venv ...
    py -3 -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat || goto :fail
python --version
python -m pip install --upgrade pip || goto :fail
python -m pip install -r roster_builder\packaging\requirements.txt pyinstaller || goto :fail
python -m PyInstaller roster_builder\packaging\RosterBuilder.spec --noconfirm --clean || goto :fail

if not exist dist\RosterBuilder\RosterBuilder.exe goto :fail

REM ---- version = roster_builder\runtime.py  __version__
set "APPVER="
for /f "usebackq delims=" %%v in (`python -c "import re;print(re.search(r'__version__\s*=\s*.(\d[\w.]*)',open(r'roster_builder\runtime.py',encoding='utf-8').read()).group(1))"`) do set "APPVER=%%v"
if "%APPVER%"=="" set "APPVER=1.0.0"
echo Version: %APPVER%

REM ---- Inno Setup 6 (ISCC.exe)
call :findiscc
if defined ISCC goto :haveiscc
echo.
echo Inno Setup 6 was not found. Trying to install it with winget ...
winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
call :findiscc
if defined ISCC goto :haveiscc
echo.
echo ============================================================
echo  The program is built, but the installer could NOT be made:
echo  Inno Setup 6 is not installed and winget could not install it.
echo  1. Download and install Inno Setup 6 from  https://jrsoftware.org/isdl.php
echo  2. Run this file again.
echo  Meanwhile the program folder is usable as it is:
echo    %CD%\dist\RosterBuilder\RosterBuilder.exe
echo    (copy the WHOLE folder dist\RosterBuilder to the other PC)
echo ============================================================
pause
exit /b 1

:haveiscc
echo Using %ISCC%
"%ISCC%" /DAppVersion=%APPVER% roster_builder\packaging\RosterBuilder.iss || goto :fail
if not exist "dist\installer\RosterBuilder_Setup_%APPVER%.exe" goto :fail
echo.
echo ============================================================
echo  Done.
echo  Installer (give this ONE file to the manager):
echo    %CD%\dist\installer\RosterBuilder_Setup_%APPVER%.exe
echo  Program folder (works without installing):
echo    %CD%\dist\RosterBuilder\RosterBuilder.exe
echo  Quick check:  dist\RosterBuilder\RosterBuilder.exe --smoke-test   (then: echo %%errorlevel%%  -> 0 = fine)
echo ============================================================
pause
exit /b 0

:findiscc
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
exit /b 0

:fail
echo.
echo ============================================================
echo  BUILD FAILED. Scroll up to read the first error message.
echo ============================================================
pause
exit /b 1
