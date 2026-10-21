@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -u -X utf8 tools\launch.py %*
  goto done
)
where python >nul 2>nul
if not errorlevel 1 (
  python -u -X utf8 tools\launch.py %*
  goto done
)
echo Python 3.11+ is required. Please install Python and reopen this launcher.
exit /b 1
:done
if errorlevel 1 (
  echo Launch failed. See the message above. Press any key to close.
  pause >nul
  exit /b 1
)
