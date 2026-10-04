# -*- mode: python ; coding: utf-8 -*-
"""Shared full/lite build. Run through build.bat or build_lite.bat."""
import os
import sys
sys.path.insert(0, SPECPATH)
from PyInstaller.utils.hooks import collect_all
from packaging_runtime import normalize_runtime_entries

channel = os.environ.get("LYRIO_BUILD_CHANNEL")
if channel not in ("full", "lite"):
    raise ValueError("Run build.bat or build_lite.bat to select an edition")

packages = ["customtkinter", "winrt", "shazamio", "segno", "pykakasi",
            "pypinyin", "korean_romanizer"]
ai_packages = ["faster_whisper", "ctranslate2", "tokenizers",
               "huggingface_hub", "onnxruntime", "av"]
if channel == "full":
    packages += ["syncedlyrics", "rapidfuzz"] + ai_packages
hiddenimports = ["winrt.windows.media.control", "winrt.windows.foundation",
                 "winrt.windows.foundation.collections", "winrt.windows.storage.streams",
                 "pystray._win32", "pyaudiowpatch", "numpy", "audioop", "aiohttp"]
datas, binaries = [], []
for package in packages:
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(["main.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=ai_packages if channel == "lite" else [],
             noarchive=False, optimize=0)
# Analysis also discovers runtimes through transitive native dependencies.
# Normalize after that discovery so no older adjacent copy can be left behind.
a.binaries = normalize_runtime_entries(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="Lyrio" if channel == "full" else "Lyrio-Lite",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
          argv_emulation=False, target_arch=None, codesign_identity=None,
          entitlements_file=None, icon=["icon.ico"])
