# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Roster Builder desktop app (PySide6, windowed, one-folder).
#   run roster_builder\packaging\build_exe.bat      (or, from the project folder:
#   pyinstaller roster_builder/packaging/RosterBuilder.spec --noconfirm --clean)
# Result: dist/RosterBuilder/RosterBuilder.exe (Windows).  One-folder starts faster and is more robust with
# ortools' native libraries than one-file; zip the dist/RosterBuilder folder to ship it.
#
# * Entry point: roster_builder/__main__.py (Qt app wrapped in a crash-log handler; "--smoke-test" flag).
# * PySide6: PyInstaller's built-in hooks collect Qt, its plugins (platforms, styles, imageformats, printsupport)
#   and shiboken6.  Qt modules the app does not use are excluded to keep the folder small.
# * The theme's arrow / check-box PNGs are written to a temp folder at run time (ui/theme.py) - no data files needed.
# * Data files: ortools, reportlab, openpyxl, arabic_reshaper package data; USER_GUIDE.md + USER_GUIDE_AR.md + ONLY the
#   screenshots they show + app icon.
# * Arabic: arabic-reshaper + python-bidi (PDF text shaping) are pure Python and collected below.
# * The installer (RosterBuilder.iss, Inno Setup) takes the finished folder dist/RosterBuilder; build_exe.bat runs it.
# * tkinter is not used and is excluded.
import os
import re

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))  # project folder (contains roster_builder/)
PKG = os.path.join(ROOT, "roster_builder")
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# ---- version (single source: roster_builder/runtime.py)
_src = open(os.path.join(PKG, "runtime.py"), encoding="utf-8").read()
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _src).group(1)
_nums = (VERSION.split(".") + ["0", "0", "0", "0"])[:4]
VERSION_TUPLE = tuple(int(re.sub(r"\D", "", n) or 0) for n in _nums)

version_info = None
try:
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Roster Builder"),
                StringStruct("FileDescription", "Roster Builder - monthly duty roster planner"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "RosterBuilder"),
                StringStruct("LegalCopyright", "Roster Builder"),
                StringStruct("OriginalFilename", "RosterBuilder.exe"),
                StringStruct("ProductName", "Roster Builder"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
except Exception:  # noqa: BLE001 - version resource is cosmetic (and only used on Windows)
    version_info = None

# ---- data files and native libraries
datas = collect_data_files("ortools") + collect_data_files("reportlab") + collect_data_files("openpyxl")
datas += collect_data_files("arabic_reshaper") + collect_data_files("bidi")
datas += [
    (os.path.join(PKG, "packaging", "USER_GUIDE.md"), os.path.join("roster_builder", "packaging")),
    (os.path.join(PKG, "packaging", "USER_GUIDE_AR.md"), os.path.join("roster_builder", "packaging")),
    (os.path.join(PKG, "packaging", "app.png"), os.path.join("roster_builder", "packaging")),
]
# only the screenshots the two guides show (the guides reference them as ../screens/<file>)
_shots = set()
for _g in ("USER_GUIDE.md", "USER_GUIDE_AR.md"):
    _shots |= set(re.findall(r"\]\(\.\./screens/([^)\s]+)\)", open(os.path.join(PKG, "packaging", _g), encoding="utf-8").read()))
for _s in sorted(_shots):
    datas.append((os.path.join(PKG, "screens", _s), os.path.join("roster_builder", "screens")))
binaries = collect_dynamic_libs("ortools")

hiddenimports = (
    collect_submodules("roster_builder")
    + collect_submodules("ortools.sat")
    + collect_submodules("ortools.util")
    + collect_submodules("reportlab")
    + collect_submodules("arabic_reshaper")
    + collect_submodules("bidi")
    + ["openpyxl.cell._writer"]
)

# ---- keep the folder small: Qt modules the app never imports, and other unused packages
QT_UNUSED = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtWebView",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtMultimedia", "QtMultimediaWidgets", "QtSpatialAudio", "QtTextToSpeech",
    "QtQuick", "QtQuickWidgets", "QtQuick3D", "QtQuickControls2", "QtQml", "QtQmlCore", "QtQmlModels", "QtQmlWorkerScript",
    "QtCharts", "QtDataVisualization", "QtGraphs", "QtGraphsWidgets",
    "QtBluetooth", "QtNfc", "QtSensors", "QtSerialPort", "QtSerialBus", "QtPositioning", "QtLocation",
    "QtRemoteObjects", "QtScxml", "QtStateMachine", "QtHttpServer", "QtNetworkAuth", "QtVirtualKeyboard",
    "QtPdf", "QtPdfWidgets", "QtSql", "QtTest", "QtDesigner", "QtHelp", "QtShaderTools", "QtSvg", "QtSvgWidgets",
    "QtUiTools", "QtAxContainer", "QtConcurrent", "QtDBus", "QtXml", "QtOpenGL", "QtOpenGLWidgets",
]
excludes = (
    ["PySide6." + m for m in QT_UNUSED]
    + ["tkinter", "_tkinter", "pytest", "pytestqt", "pypdf", "matplotlib", "IPython", "scipy", "PyQt5", "PyQt6", "PySide2"]
)

a = Analysis(
    [os.path.join(PKG, "__main__.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# Qt plugins pull in libraries for features this app does not use (QML / Quick, PDF viewer, SVG, on-screen keyboard,
# software OpenGL).  Dropping them saves ~60 MB; the app only needs Core, Gui, Widgets and PrintSupport.
_DROP = ("qt6qml", "qt6quick", "qt6pdf", "qt6svg", "qt6virtualkeyboard", "qtvirtualkeyboard", "qsvg", "qpdf", "opengl32sw")
a.binaries = [b for b in a.binaries if not any(k in os.path.basename(b[0]).lower() for k in _DROP)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RosterBuilder",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # windowed: no console window (start-up errors go to the log file + a message box)
    icon=os.path.join(PKG, "packaging", "app.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RosterBuilder",
)
