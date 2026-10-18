@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scanner.py %*
) else (
  python scanner.py %*
)
echo.
echo Scanner beendet. Taste druecken, um Fenster zu schliessen.
pause >nul
