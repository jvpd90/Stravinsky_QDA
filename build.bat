@echo off
rem Double-click or run from a terminal: builds the app, the portable zip and
rem the installer. Arguments are passed to build.ps1 (e.g. build.bat -SkipTests).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 pause
