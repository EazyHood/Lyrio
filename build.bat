@echo off
setlocal
rem Compila Lyrio.exe (PyInstaller). Resultado: dist\Lyrio.exe
cd /d "%~dp0"
if errorlevel 1 exit /b 1
>_build_info.py echo UPDATE_CHANNEL = 'full'
if not exist _build_info.py exit /b 1
python -m PyInstaller --noconfirm --onefile --noconsole --name Lyrio --workpath build/full ^
  --icon icon.ico ^
  --collect-all customtkinter ^
  --collect-all winrt ^
  --hidden-import winrt.windows.media.control ^
  --hidden-import winrt.windows.foundation ^
  --hidden-import winrt.windows.foundation.collections ^
  --hidden-import winrt.windows.storage.streams ^
  --hidden-import pystray._win32 ^
  --hidden-import syncedlyrics ^
  --collect-all syncedlyrics ^
  --collect-all rapidfuzz ^
  --hidden-import pyaudiowpatch ^
  --hidden-import numpy ^
  --collect-all shazamio ^
  --hidden-import audioop ^
  --hidden-import aiohttp ^
  --collect-all segno ^
  --collect-all pykakasi ^
  --collect-all pypinyin ^
  --collect-all korean_romanizer ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all tokenizers ^
  --collect-all huggingface_hub ^
  --collect-all onnxruntime ^
  --collect-all av ^
  main.py
set "BUILD_EXIT=%ERRORLEVEL%"
del /q _build_info.py 2>nul
if not "%BUILD_EXIT%"=="0" exit /b %BUILD_EXIT%
echo.
echo Listo: %~dp0dist\Lyrio.exe
exit /b 0
