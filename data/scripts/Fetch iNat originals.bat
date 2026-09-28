@echo off
rem Downloads full-size copies of every species photo to C:\PSBP\data\media.
rem Safe to close and run again; finished files are skipped.
cd /d "%~dp0..\.."
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% "data\scripts\fetch_inat_originals.py" %*
echo.
pause
