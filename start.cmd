@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -B -u -X utf8 tools\launch.py %*
  goto done
)
where python >nul 2>nul
if not errorlevel 1 (
  python -B -u -X utf8 tools\launch.py %*
  goto done
)
echo 未找到 Python。请安装 Python 3.11 或以上，并勾选 Add Python to PATH，再重新打开启动器。
pause >nul
exit /b 1
:done
if errorlevel 1 (
  echo Launch failed. See the message above. Press any key to close.
  pause >nul
  exit /b 1
)
