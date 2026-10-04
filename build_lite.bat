@echo off
setlocal
rem Lyrio Lite: sin la IA de sincronizacion (Whisper) — exe mucho mas ligero.
rem Todo lo demas funciona igual (Shazam, fuentes, overlay, traduccion...).
cd /d "%~dp0"
if errorlevel 1 exit /b 1
>_build_info.py echo UPDATE_CHANNEL = 'lite'
if not exist _build_info.py exit /b 1
python -m PyInstaller --noconfirm --onefile --noconsole --name Lyrio-Lite --workpath build/lite ^
  --icon icon.ico ^
  --collect-all customtkinter ^
  --collect-all winrt ^
  --hidden-import winrt.windows.media.control ^
  --hidden-import winrt.windows.foundation ^
  --hidden-import winrt.windows.foundation.collections ^
  --hidden-import winrt.windows.storage.streams ^
  --hidden-import pystray._win32 ^
  --hidden-import pyaudiowpatch ^
  --hidden-import numpy ^
  --collect-all shazamio ^
  --hidden-import audioop ^
  --hidden-import aiohttp ^
  --collect-all segno ^
  --collect-all pykakasi ^
  --collect-all pypinyin ^
  --collect-all korean_romanizer ^
  --exclude-module faster_whisper ^
  --exclude-module ctranslate2 ^
  --exclude-module onnxruntime ^
  --exclude-module av ^
  --exclude-module tokenizers ^
  --exclude-module huggingface_hub ^
  main.py
set "BUILD_EXIT=%ERRORLEVEL%"
del /q _build_info.py 2>nul
if not "%BUILD_EXIT%"=="0" exit /b %BUILD_EXIT%
echo Listo: %~dp0dist\Lyrio-Lite.exe
exit /b 0
