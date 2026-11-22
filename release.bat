@echo off
rem Publish a new version:  release.bat 1.0.3
rem Sets version.py, runs the tests, commits, tags and pushes; GitHub then
rem builds and publishes the installer and the zip.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release.ps1" %*
pause
