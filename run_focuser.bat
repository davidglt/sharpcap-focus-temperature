@echo off
REM run_focuser.bat
REM SharpCap Advanced Sequencer wrapper for sharpcap_focuser.py
REM All arguments are forwarded to the script, e.g.:
REM   run_focuser.bat --last-days 30 --predict-temperature 15 --auto-axis

set DIR=C:\astro\sharpcap-focus-temperature
set PYTHON=%DIR%\.venv\Scripts\python.exe
set SCRIPT=%DIR%\sharpcap_focuser.py

cd /d %DIR%
"%PYTHON%" "%SCRIPT%" %*
