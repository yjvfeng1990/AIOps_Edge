@echo off
rem ============================================================
rem  EdgeOps Gateway Ops Agent launcher
rem  Pure ASCII on purpose: safe under GBK/UTF-8 console
rem  codepages. No chcp switch, no Chinese text in this file.
rem ============================================================
setlocal enableextensions
cd /d "%~dp0"

set "PY="

rem ---- 1. locate a usable Python 3.9+ ----
rem every python.exe on PATH first (version check skips the
rem Microsoft Store python stub, which always exits non-zero)
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
        "%%i" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=%%i"
    )
)
rem then: common per-user install locations
if not defined PY call :probe "%LocalAppData%\Programs\Python\Python314\python.exe"
if not defined PY call :probe "%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PY call :probe "%LocalAppData%\Programs\Python\Python312\python.exe"

if not defined PY (
    echo [ERROR] Python 3.11+ not found on this machine.
    echo.
    echo Install Python from https://www.python.org/downloads/windows/
    echo and tick "Add python.exe to PATH" in the installer,
    echo then double-click this file again.
    echo.
    echo Or start from a terminal:  python run_agent.py
    pause
    exit /b 1
)

echo Using Python: %PY%

rem ---- 2. first run: install core dependencies ----
"%PY%" -c "import fastapi, uvicorn, requests" >nul 2>nul
if errorlevel 1 (
    echo First run: installing dependencies, please wait...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check network/proxy, then retry.
        pause
        exit /b 1
    )
)

rem ---- 3. tray mode needs pythonw + pystray ----
set "PYW="
for %%i in ("%PY%") do (
    if exist "%%~dpipythonw.exe" set "PYW=%%~dpipythonw.exe"
)
set "TRAY=1"
if not defined PYW set "TRAY="
if defined TRAY (
    "%PY%" -c "import pystray, PIL" >nul 2>nul
    if errorlevel 1 (
        "%PY%" -m pip install pystray Pillow >nul 2>nul
        "%PY%" -c "import pystray, PIL" >nul 2>nul
        if errorlevel 1 set "TRAY="
    )
)

rem ---- 4. launch ----
if defined TRAY (
    start "" "%PYW%" run_agent.py
) else (
    echo Tray unavailable, running in console mode. Keep this window open.
    "%PY%" run_agent.py
)

endlocal
exit /b 0

:probe
if not defined PY (
    if exist "%~1" (
        "%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=%~1"
    )
)
goto :eof
