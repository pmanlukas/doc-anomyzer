@echo off
rem Optionales KI-Zusatzmodell GLiNER installieren (ca. 2 GB, einmalig Internet).
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Bitte zuerst "Installieren.bat" ausfuehren.
    pause
    exit /b 1
)
echo PyTorch (CPU-Version) wird installiert ...
".venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet || goto :error
".venv\Scripts\python.exe" -m pip install -r requirements-ki.txt --quiet || goto :error
".venv\Scripts\python.exe" -m doc_anonymizer.setup_helper gliner || goto :error
echo.
echo Zusatzmodell installiert. Es wird ab der naechsten Analyse automatisch verwendet.
pause
exit /b 0
:error
echo [FEHLER] Installation des Zusatzmodells fehlgeschlagen.
pause
exit /b 1
