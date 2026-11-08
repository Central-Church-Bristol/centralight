@echo off
rem Start CentraLight. The first time, this sets up .venv and copies the church fonts.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  py -3 -m venv .venv
)
".venv\Scripts\python.exe" -c "import PySide6, cv2, numpy, NDIlib, serial, stupidArtnet, pyaudiowpatch" 2>nul || ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist "fonts\FuturaCyrillicBook.ttf" call :copyfonts
if not exist ".venv\Scripts\pythonw.exe" (
  echo CentraLight couldn't set up its Python ^(.venv^). Is Python 3.14 installed?
  pause
  exit /b 1
)
rem CentraLight.exe runs Python inside itself, so Windows sees CentraLight (8 Nov)
if exist "CentraLight.exe" (
  start "" "CentraLight.exe" %*
) else (
  start "" ".venv\Scripts\pythonw.exe" main.py %*
)
exit /b

:copyfonts
rem Copied once, so the app never reads Google Drive while it runs
set "BRAND=G:\My Drive\Video Projection\Brand\Fonts"
if not exist "%BRAND%\Futura PT\FuturaCyrillicBook.ttf" exit /b
if not exist fonts mkdir fonts
for %%F in (FuturaCyrillicBook FuturaCyrillicMedium FuturaCyrillicDemi FuturaCyrillicBold) do copy /y "%BRAND%\Futura PT\%%F.ttf" fonts\ >nul
exit /b
