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
echo [1/3] Alte Builds loeschen...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [2/3] PyInstaller: onedir-Build...
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
echo [3/3] Inno Setup: Installer bauen...
%ISCC% setup.iss
if errorlevel 1 goto :error

echo.
echo ==============================================
echo Fertig!
echo   Onedir:    dist\ClaudeSessionBrowser\
echo   Installer: dist\ClaudeSessionBrowser-Setup.exe
echo ==============================================
pause
exit /b 0

:error
echo.
echo BUILD FEHLGESCHLAGEN.
pause
exit /b 1
