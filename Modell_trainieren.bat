@echo off
rem Sprachmodell mit den eigenen Entscheidungen nachtrainieren (lokal).
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m doc_anonymizer trainieren
pause
