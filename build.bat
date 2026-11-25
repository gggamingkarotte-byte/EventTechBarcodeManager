@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHON_ARGS="
if not exist "%PYTHON%" set "PYTHON="
if not defined PYTHON (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYTHON set "PYTHON=%%P"
  )
)
if not defined PYTHON (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON=py"
    set "PYTHON_ARGS=-3"
  )
)
if not defined PYTHON (
  echo Python 3 wurde nicht gefunden. Installiere Python 3.11 oder neuer.
  exit /b 1
)

echo [1/3] Pruefe Python-Abhaengigkeiten...
echo Verwende: "%PYTHON%" %PYTHON_ARGS%
"%PYTHON%" %PYTHON_ARGS% -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo [2/3] Erstelle Windows-Anwendung...
"%PYTHON%" %PYTHON_ARGS% -m PyInstaller --noconfirm --clean "EventTechBarcodeManager.spec"
if errorlevel 1 goto failed

echo [3/3] Erstelle Installer, falls Inno Setup verfuegbar ist...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC (
  "%ISCC%" /Qp "installer.iss"
  if errorlevel 1 goto failed
  echo Installer: dist\installer\EventTechBarcodeManager-Setup-1.1.0.exe
) else (
  echo Inno Setup 6 nicht gefunden. Anwendung liegt bereit in:
  echo dist\EventTechBarcodeManager\EventTechBarcodeManager.exe
  echo Installiere Inno Setup 6 und starte build.bat erneut fuer den Installer.
  exit /b 1
)

echo.
echo Build abgeschlossen.
exit /b 0

:failed
echo.
echo Build fehlgeschlagen. Obige Fehlermeldung pruefen.
exit /b 1
