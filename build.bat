@echo off
rem Compila Lyrio.exe (PyInstaller). Resultado: dist\Lyrio.exe
cd /d "%~dp0"
rmdir /s /q build dist 2>nul
python -m PyInstaller --noconfirm --onefile --noconsole --name Lyrio ^
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
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all tokenizers ^
  --collect-all huggingface_hub ^
  --collect-all onnxruntime ^
  --collect-all av ^
  main.py
echo.
echo Listo: %~dp0dist\Lyrio.exe
