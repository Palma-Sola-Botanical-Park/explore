@echo off
rem First-run rehearsal: three photos to the SANDBOX bucket, then stop.
rem Check them at https://pub-895c4e39efa04b698caca4bce36ba281.r2.dev/inat/<photo_id>/v1/web.jpg
rem The real run is "Upload media to R2.bat" next to this file.
cd /d "%~dp0..\.."
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -c "import PIL" >nul 2>nul || %PY% -m pip install pillow
%PY% "data\scripts\upload_r2_media.py" --bucket psbp-sandbox --limit 3
echo.
pause
