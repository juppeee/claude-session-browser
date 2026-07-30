@echo off
REM Build-Script fuer Claude Session Browser
REM ---------------------------------------
REM Baut die App als "onedir" (schnelle Startzeit, kein Temp-Extract)
REM und verpackt sie mit Inno Setup zu einem echten Installer.
REM
REM Voraussetzungen (einmalig):
REM   pip install pyinstaller pywebview pystray Pillow
REM   winget install JRSoftware.InnoSetup

setlocal
set TCL_LIBRARY=%LOCALAPPDATA%\Programs\Python\Python311\tcl\tcl8.6
set SETUPTOOLS_USE_DISTUTILS=stdlib
set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

echo.
echo [1/4] Alte Builds loeschen...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [2/4] PyInstaller: onedir-Build (fuer Installer)...
pyinstaller ^
  --onedir ^
  --noconsole ^
  --clean ^
  --name ClaudeSessionBrowser ^
  --icon claude_sessions.ico ^
  --add-data "claude_sessions.ico;." ^
  --hidden-import pystray ^
  --hidden-import PIL ^
  claude_sessions.py
if errorlevel 1 goto :error

echo.
echo [3/5] Separater Updater (setup.iss erwartet dist\csb_updater.exe)...
pyinstaller --clean --noconfirm csb_updater.spec
if errorlevel 1 goto :error

echo.
echo [4/5] Inno Setup: Installer bauen...
%ISCC% setup.iss
if errorlevel 1 goto :error

echo.
echo [5/5] PyInstaller: Onefile-Fallback (fuer alte v1.0.x-Auto-Updater)...
REM Zweiter Build als Onefile - alte Versionen die noch keinen installer_url
REM kennen laden diesen via exe_url wie eine normale App-EXE. Diese Version
REM checkt beim Start ob eine echte Installation da ist und delegiert sonst
REM an den Installer.
pyinstaller ^
  --onefile ^
  --noconsole ^
  --clean ^
  --name ClaudeSessionBrowser-Onefile ^
  --icon claude_sessions.ico ^
  --add-data "claude_sessions.ico;." ^
  --hidden-import pystray ^
  --hidden-import PIL ^
  claude_sessions.py
if errorlevel 1 goto :error
REM Zum Ausliefern umbenennen (exe_url zeigt in alten Versionen auf diesen Namen)
if exist dist\ClaudeSessionBrowser.exe del /f /q dist\ClaudeSessionBrowser.exe
move /y dist\ClaudeSessionBrowser-Onefile.exe dist\ClaudeSessionBrowser.exe

echo.
echo ==============================================
echo Fertig!
echo   Installer:        dist\ClaudeSessionBrowser-Setup.exe  (neuer Weg)
echo   Onefile-Fallback: dist\ClaudeSessionBrowser.exe        (fuer alte v1.0.x)
echo ==============================================
pause
exit /b 0

:error
echo.
echo BUILD FEHLGESCHLAGEN.
pause
exit /b 1
