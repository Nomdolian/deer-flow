@echo off
REM ===================================================================
REM  24/7 supervisor for the MT5 trading runner.
REM
REM  Restarts the runner if it exits for any reason. The runner already
REM  survives individual bad cycles internally; this catches the harder
REM  failures - the MT5 terminal being killed, a Python-level crash, an
REM  OOM - so the system comes back on its own instead of sitting dead
REM  until you happen to look at the laptop.
REM
REM  Edit PROJECT_DIR below to wherever you cloned the repo.
REM ===================================================================

set PROJECT_DIR=C:\trading-system
set POLL_SECONDS=30
set RESTART_DELAY_SECONDS=15
set LOG_DIR=%PROJECT_DIR%\logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

cd /d "%PROJECT_DIR%"

:loop
echo [%date% %time%] starting trading runner >> "%LOG_DIR%\supervisor.log"

REM Timestamped log per run so a crash loop doesn't overwrite the evidence.
set LOGSTAMP=%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set LOGSTAMP=%LOGSTAMP: =0%

call .venv\Scripts\python.exe -m scripts.run_mt5_live --poll-seconds %POLL_SECONDS% ^
  >> "%LOG_DIR%\runner_%LOGSTAMP%.log" 2>&1

echo [%date% %time%] runner exited with code %errorlevel%, restarting in %RESTART_DELAY_SECONDS%s >> "%LOG_DIR%\supervisor.log"
timeout /t %RESTART_DELAY_SECONDS% /nobreak > nul
goto loop
