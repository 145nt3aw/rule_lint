# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Rule Lint GUI.
#
# Build:
#     pyinstaller rule_lint.spec
#
# Or:
#     python3 build_release.py
#
# Output:
#     dist/RuleLint              (Linux)
#     dist/RuleLint.exe          (Windows)
#     dist/RuleLint.app/         (macOS bundle, when --windowed)

import os
import sys
from pathlib import Path

HERE = Path(SPECPATH).resolve()

# rule_catalogue.py is imported by rule_lint.py and rule_lint_gui.py — we
# include it as both a data file AND a hidden import so PyInstaller picks it
# up whichever way Python resolves it.
datas = [
    (str(HERE / "rule_catalogue.py"), "."),
]

hiddenimports = [
    "rule_lint",
    "rule_catalogue",
    # tkinter is stdlib but PyInstaller sometimes needs an explicit hint
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
]


block_cipher = None


a = Analysis(
    [str(HERE / "rule_lint_gui.py")],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Slim down the binary.
        "test", "unittest", "pydoc_data", "lib2to3",
        "pip", "setuptools", "wheel",
        "numpy", "pandas", "matplotlib", "scipy",
        "PIL", "PyQt5", "PyQt6", "PySide2", "PySide6",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RuleLint",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # GUI app — no console window on Windows
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)


# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="RuleLint.app",
        icon=None,
        bundle_identifier="com.github.rule_lint",
        info_plist={
            "CFBundleDisplayName": "Rule Lint",
            "CFBundleName": "Rule Lint",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1.0",
            "NSHighResolutionCapable": True,
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Rule Equation",
                    "CFBundleTypeExtensions": ["eq", "rule", "mask"],
                    "CFBundleTypeRole": "Editor",
                }
            ],
        },
    )
