@echo off
REM ===================================================================
REM  Double-click this file. It does the parts a web page can't:
REM  finds or installs Python, creates the environment, installs the
REM  packages, then opens the setup page in your browser where the rest
REM  is buttons.
REM
REM  Safe to run again — it skips anything already done.
REM ===================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0..\.."

echo ============================================================
echo   Trading system setup
echo ============================================================
echo.

REM ---------- 1. Python -----------------------------------------------
set PY=
for %%V in (3.12 3.13) do (
  if not defined PY (
    py -%%V --version >nul 2>&1 && set PY=py -%%V
  )
)

if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo Found Python !PYVER! on PATH.
    echo !PYVER! | findstr /r "^3\.1[23]\." >nul && set PY=python
  )
)

if not defined PY (
  echo Python 3.12 or 3.13 was not found. Trying to install it with winget...
  echo.
  winget --version >nul 2>&1
  if errorlevel 1 (
    echo   winget is not available on this machine.
    echo.
    echo   Install Python 3.12 from https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add python.exe to PATH" during the install.
    echo   Then close this window and double-click this file again.
    echo.
    pause
    exit /b 1
  )
  winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
  echo.
  echo Python was installed. Windows needs a new window to see it.
  echo Close this window and double-click this file again.
  echo.
  pause
  exit /b 0
)

echo Using: %PY%
%PY% --version
echo.

REM ---------- 2. Virtual environment ----------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo Could not create the environment. See the error above.
    pause
    exit /b 1
  )
) else (
  echo Environment already exists.
)

set VENVPY=.venv\Scripts\python.exe

REM ---------- 3. Packages --------------------------------------------
echo.
echo Installing packages. First run takes a few minutes.
echo.
"%VENVPY%" -m pip install --upgrade pip --quiet
"%VENVPY%" -m pip install -e ".[mt5]"
if errorlevel 1 (
  echo.
  echo ------------------------------------------------------------
  echo  Package install failed.
  echo.
  echo  If the error mentions ABI tags or "no matching distribution"
  echo  for MetaTrader5, your Python version has no wheel for it.
  echo  Install Python 3.12 and run this file again.
  echo ------------------------------------------------------------
  pause
  exit /b 1
)

REM ---------- 4. Hand over to the browser -----------------------------
echo.
echo ============================================================
echo   Opening the setup page in your browser.
echo.
echo   Leave THIS window open while you use it.
echo   Everything else happens on the page.
echo ============================================================
echo.

"%VENVPY%" -m scripts.setup_wizard

echo.
echo Setup page closed.
pause
