@echo off
setlocal
rem Lyrio Lite: sin la IA de sincronizacion (Whisper).
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "LYRIO_BUILD_CHANNEL=lite"
>_build_info.py echo UPDATE_CHANNEL = 'lite'
if not exist _build_info.py exit /b 1
python -m PyInstaller --noconfirm --workpath build/lite lyrio-build.spec
set "BUILD_EXIT=%ERRORLEVEL%"
del /q _build_info.py 2>nul
if not "%BUILD_EXIT%"=="0" exit /b %BUILD_EXIT%
echo Listo: %~dp0dist\Lyrio-Lite.exe
exit /b 0
