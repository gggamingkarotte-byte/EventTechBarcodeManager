@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=C:\Python315\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [1/3] Pruefe Python-Abhaengigkeiten...
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo [2/3] Erstelle Windows-Anwendung...
"%PYTHON%" -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name "EventTechBarcodeManager" ^
  --icon "assets\icon.ico" ^
  --hidden-import barcode.codex ^
  --hidden-import barcode.ean ^
  --collect-all zxingcpp ^
  main.py
if errorlevel 1 goto failed

echo [3/3] Erstelle Installer, falls Inno Setup verfuegbar ist...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC (
  "%ISCC%" "installer.iss"
  if errorlevel 1 goto failed
  echo Installer: dist\installer\EventTechBarcodeManager-Setup-1.0.6.exe
) else (
  echo Inno Setup 6 nicht gefunden. Anwendung liegt bereit in:
  echo dist\EventTechBarcodeManager\EventTechBarcodeManager.exe
  echo Installiere Inno Setup 6 und starte build.bat erneut fuer den Installer.
)

echo.
echo Build abgeschlossen.
exit /b 0

:failed
echo.
echo Build fehlgeschlagen. Obige Fehlermeldung pruefen.
exit /b 1
