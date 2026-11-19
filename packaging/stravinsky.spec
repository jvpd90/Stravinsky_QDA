# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Stravinsky.exe -- run through build.ps1, not directly.

Builds a one-folder app (dist/Stravinsky/): it starts much faster than a
one-file exe, which unpacks itself to a temp folder on every launch, and it
is what the installer and the portable zip ship.

The package must sit in a folder named Stravinsky_QDA (the import name).
"""
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32 import versioninfo as vi

PACKAGE = "Stravinsky_QDA"
ROOT = Path(SPECPATH).resolve().parent          # the package folder
if ROOT.name != PACKAGE:
    raise SystemExit(f"The project folder must be named '{PACKAGE}' (it is '{ROOT.name}').")

version_text = (ROOT / "version.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', version_text).group(1)
APP_NAME = re.search(r'APP_NAME\s*=\s*"([^"]+)"', version_text).group(1)
numbers = tuple(int(n) for n in re.findall(r"\d+", VERSION)[:3]) + (0,)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT.parent)],
    binaries=[],
    # Data next to the package's modules, where the code looks for it
    # (Path(__file__).parent.parent / "assets" and / "help").
    datas=[
        (str(ROOT / "assets"), f"{PACKAGE}/assets"),
        (str(ROOT / "help"), f"{PACKAGE}/help"),
    ],
    hiddenimports=collect_submodules(PACKAGE, filter=lambda name: ".tests" not in name)
    + collect_submodules("scipy._external.array_api_compat"),
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["TkAgg", "Agg"]}},
    runtime_hooks=[],
    excludes=["prince", "sklearn", "pytest", "IPython", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

version_info = vi.VSVersionInfo(
    ffi=vi.FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[
        vi.StringFileInfo([vi.StringTable("040904B0", [
            vi.StringStruct("CompanyName", APP_NAME),
            vi.StringStruct("FileDescription", f"{APP_NAME} - Qualitative Data Analysis"),
            vi.StringStruct("FileVersion", VERSION),
            vi.StringStruct("InternalName", APP_NAME),
            vi.StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            vi.StringStruct("ProductName", APP_NAME),
            vi.StringStruct("ProductVersion", VERSION),
        ])]),
        vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed exes trigger antivirus false positives
    console=False,      # windowed: no console window behind the app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=version_info,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
