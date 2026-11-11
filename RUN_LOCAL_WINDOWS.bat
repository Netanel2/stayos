@echo off
cd /d "%~dp0"
if "%STAYOS_ADMIN_PASSWORD%"=="" set STAYOS_ADMIN_PASSWORD=local-demo-only-change-me
python -m pip install -r requirements.txt
python server.py
pause
