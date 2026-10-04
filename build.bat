@echo off
setlocal
rem Compila Lyrio.exe (PyInstaller). Resultado: dist\Lyrio.exe
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "LYRIO_BUILD_CHANNEL=full"
>_build_info.py echo UPDATE_CHANNEL = 'full'
if not exist _build_info.py exit /b 1
python -m PyInstaller --noconfirm --workpath build/full lyrio-build.spec
set "BUILD_EXIT=%ERRORLEVEL%"
del /q _build_info.py 2>nul
if not "%BUILD_EXIT%"=="0" exit /b %BUILD_EXIT%
echo.
echo Listo: %~dp0dist\Lyrio.exe
exit /b 0
