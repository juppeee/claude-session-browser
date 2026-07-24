@echo off
REM Baut die eigenstaendige EXE (benoetigt: pip install pyinstaller pywebview)
pyinstaller --onefile --noconsole --clean --name ClaudeSessionBrowser --icon claude_sessions.ico --add-data "claude_sessions.ico;." --hidden-import pystray --hidden-import PIL claude_sessions.py
echo.
echo Fertig: dist\ClaudeSessionBrowser.exe
pause
