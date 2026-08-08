@echo off
rem ================================================================
rem  nouhin-check (Windows droplet)
rem  Usage: drag & drop video files onto this file, or onto a
rem  desktop shortcut pointing to this file.
rem  Requires: Python 3 (python.org) and ffmpeg (winget install Gyan.FFmpeg)
rem ================================================================
set "SCRIPT=%~dp0nouhin_gui.py"

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%SCRIPT%" %*
  goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
  start "" py -3 "%SCRIPT%" %*
  goto :eof
)

python "%SCRIPT%" %*
