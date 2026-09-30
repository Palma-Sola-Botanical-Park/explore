@echo off
rem Makes web + thumb copies of every fetched original and uploads all three
rem to the park's Cloudflare R2 bucket. Safe to close and run again.
rem
rem Needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY set for this
rem Windows account. Default bucket is the sandbox; for production run
rem     "Upload media to R2.bat" --bucket psbp-public
cd /d "%~dp0..\.."
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -c "import PIL" >nul 2>nul || %PY% -m pip install pillow
%PY% "data\scripts\upload_r2_media.py" %*
echo.
pause
