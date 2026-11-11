@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -m pip install -r requirements.txt
if errorlevel 1 (echo Failed to install dependencies & pause & exit /b 1)
echo.
echo -----------------------------------------------
echo STAYOS demo is starting at http://localhost:8765
 echo Staff password: stayos-demo (change with STAYOS_ADMIN_PASSWORD)
echo Leave this window running while testing.
echo -----------------------------------------------
start "" http://localhost:8765
%PY% server.py
pause
