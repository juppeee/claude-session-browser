@echo off
REM Baut die eigenstaendige EXE (benoetigt: pip install pyinstaller pywebview)
pyinstaller --onefile --noconsole --name ClaudeSessionBrowser --icon claude_sessions.ico claude_sessions.py
echo.
echo Fertig: dist\ClaudeSessionBrowser.exe
pause
