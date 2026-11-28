@echo off
rem Dokumenten-Anonymisierer starten (laeuft nur auf diesem Computer).
chcp 65001 >nul
cd /d "%~dp0"
title Dokumenten-Anonymisierer
if not exist ".venv\Scripts\python.exe" (
    echo Das Programm ist noch nicht installiert - die Installation startet jetzt.
    call "%~dp0Installieren.bat" || exit /b 1
)
".venv\Scripts\python.exe" -m doc_anonymizer start
if errorlevel 1 pause
