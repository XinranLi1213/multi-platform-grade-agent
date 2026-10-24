@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -B -X utf8 tools\desktop_console.py %*
  goto done
)
where python >nul 2>nul
if not errorlevel 1 (
  python -B -X utf8 tools\desktop_console.py %*
  goto done
)
echo 未找到Python，请安装Python 3.11及以上并包含Tcl/Tk，再重新打开本入口。
pause >nul
exit /b 1
:done
