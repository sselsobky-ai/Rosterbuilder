@echo off
REM Builds dist\RosterBuilder\RosterBuilder.exe and the installer dist\installer\RosterBuilder_Setup_<version>.exe
REM (Windows; needs Python 3.11 - 3.14, tested with 3.14 from python.org. Python 3.15 is not supported yet.)
REM Double-click it, or run it from any folder. The window stays open at the end so you can read the result.
setlocal EnableDelayedExpansion
cd /d "%~dp0..\.."
echo Project folder: %CD%
set "BLOG=roster_builder\packaging\build_log.txt"
set "STAGE=start"
> "%BLOG%" echo ==== %date% %time%  build_exe.bat

REM ---- Python: the solver library (ortools) supports Python 3.11 - 3.14 only (NOT 3.15 yet).
REM An existing .venv made with another Python version is rebuilt.
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo The existing .venv uses an unsupported Python version - rebuilding it ...
        rmdir /s /q .venv
    )
)
if not exist .venv\Scripts\python.exe (
    call :pickpython
    if not defined PYSEL (
        echo.
        echo No supported Python found. Trying to install Python 3.14 with winget ...
        winget install --id Python.Python.3.14 -e --silent --accept-package-agreements --accept-source-agreements
        call :pickpython
    )
    if not defined PYSEL (
        echo.
        echo ============================================================
        echo  A supported Python was NOT found on this computer.
        echo  This program needs Python 3.11, 3.12, 3.13 or 3.14 to be BUILT
        echo  ^(Python 3.15 does not work yet: the solver library has no 3.15 version^).
        echo  1. Install Python 3.14 from  https://www.python.org/downloads/
        echo     ^(it can be installed next to another Python version^)
        echo  2. Run this file again.
        echo  Note: a computer that only USES the program does not need Python at all -
        echo  just run RosterBuilder_Setup_x.y.z.exe made on the build computer.
        echo ============================================================
        pause
        exit /b 1
    )
    echo Creating the virtual environment .venv with: !PYSEL!
    !PYSEL! -m venv .venv || goto :fail
)
set "STAGE=activate .venv"
call .venv\Scripts\activate.bat || goto :fail
python --version
python --version >> "%BLOG%" 2>&1
set "STAGE=pip upgrade"
python -m pip install --upgrade pip || goto :fail
set "STAGE=pip install requirements"
python -m pip install -r roster_builder\packaging\requirements.txt pyinstaller || goto :fail
set "STAGE=PyInstaller"
python -m PyInstaller roster_builder\packaging\RosterBuilder.spec --noconfirm --clean || goto :fail

if not exist dist\RosterBuilder\RosterBuilder.exe goto :fail

REM ---- version = roster_builder\runtime.py  __version__
set "APPVER="
for /f "usebackq delims=" %%v in (`python -c "import re;print(re.search(r'__version__\s*=\s*.(\d[\w.]*)',open(r'roster_builder\runtime.py',encoding='utf-8').read()).group(1))"`) do set "APPVER=%%v"
if "%APPVER%"=="" set "APPVER=1.0.0"
echo Version: %APPVER%

REM ---- Inno Setup 6 (ISCC.exe)
set "STAGE=find Inno Setup"
>> "%BLOG%" echo program built OK, version %APPVER%
call :pickpython
set "PYSEL="
for %%V in (3.14 3.13 3.12 3.11) do (
    if not defined PYSEL (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PYSEL=py -%%V"
    )
)
if not defined PYSEL (
    python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYSEL=python"
)
exit /b 0

:findiscc
if defined ISCC goto :haveiscc
echo.
echo Inno Setup 6 was not found. Trying to install it with winget ...
winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
call :findiscc
if defined ISCC goto :haveiscc
echo.
echo ============================================================
>> "%BLOG%" echo Inno Setup 6 not found and winget could not install it
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
>> "%BLOG%" echo Using %ISCC%
set "STAGE=Inno Setup compile (see the ISCC messages in build_log.txt)"
"%ISCC%" /DAppVersion=%APPVER% roster_builder\packaging\RosterBuilder.iss >> "%BLOG%" 2>&1
if errorlevel 1 (
    type "%BLOG%"
    goto :fail
)
set "STAGE=installer file missing after compile"
if not exist "dist\installer\RosterBuilder_Setup_%APPVER%.exe" goto :fail
echo.
echo ============================================================
>> "%BLOG%" echo DONE
echo  Done.
echo  Installer (give this ONE file to the manager):
echo    %CD%\dist\installer\RosterBuilder_Setup_%APPVER%.exe
echo  Program folder (works without installing):
echo    %CD%\dist\RosterBuilder\RosterBuilder.exe
echo  Quick check:  dist\RosterBuilder\RosterBuilder.exe --smoke-test   (then: echo %%errorlevel%%  - 0 means fine)
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
>> "%BLOG%" echo BUILD FAILED at stage: %STAGE%
echo.
echo ============================================================
echo  BUILD FAILED at stage: %STAGE%
echo  Scroll up to read the first error message. Details: %BLOG%
echo ============================================================
pause
exit /b 1
