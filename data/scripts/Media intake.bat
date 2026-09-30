@echo off
rem Double-click to open the Media intake page (http://localhost:8703).
rem Registers park media into data\sources\media_library.json and uploads it to R2.
rem Needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY for this account,
rem or a C:\PSBP\data\media\r2.env file. Close this window to stop.
cd /d "%~dp0..\.."
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -c "import PIL" >nul 2>nul || %PY% -m pip install pillow
%PY% "data\scripts\media_intake.py" %*
pause
