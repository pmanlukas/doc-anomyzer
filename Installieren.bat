@echo off
rem ======================================================================
rem  Dokumenten-Anonymisierer - Installation (einmalig, benoetigt Internet)
rem  Danach laeuft das Programm vollstaendig ohne Internet.
rem ======================================================================
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Dokumenten-Anonymisierer - Installation

echo.
echo  Dokumenten-Anonymisierer wird installiert ...
echo  (Es werden nur Programmteile heruntergeladen - keine Dokumente gesendet.)
echo.

rem --- 1. Python suchen (3.10 bis 3.12) -----------------------------------
set "PY="
for %%V in (3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo  Python 3.12 wird installiert ...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo  [FEHLER] Python konnte nicht automatisch installiert werden.
        echo  Bitte Python 3.12 von https://www.python.org/downloads/ installieren
        echo  ^(Haken bei "Add python.exe to PATH" setzen^) und diese Datei erneut starten.
        pause
        exit /b 1
    )
    echo.
    echo  Python wurde installiert. Bitte dieses Fenster schliessen und
    echo  "Installieren.bat" noch einmal per Doppelklick starten.
    pause
    exit /b 0
)
echo  [OK] Python gefunden: %PY%

rem --- 2. Eigene Programmumgebung anlegen ---------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  Programmumgebung wird angelegt ...
    %PY% -m venv .venv || goto :error
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip --quiet || goto :error
echo  Programmteile werden installiert (dauert einige Minuten) ...
"%VPY%" -m pip install -r requirements.txt --quiet || goto :error

rem --- 3. Deutsches Sprachmodell -----------------------------------------
"%VPY%" -c "import de_core_news_lg" >nul 2>&1
if errorlevel 1 (
    echo  Deutsches Sprachmodell wird geladen ^(ca. 550 MB^) ...
    "%VPY%" -m spacy download de_core_news_lg || goto :error
)

rem --- 4. Texterkennung Tesseract ----------------------------------------
"%VPY%" -c "from doc_anonymizer import ocr; import sys; sys.exit(0 if ocr.is_available() else 1)" >nul 2>&1
if errorlevel 1 (
    echo  Texterkennung Tesseract wird installiert ...
    winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo  [HINWEIS] Bitte Tesseract manuell installieren:
        echo  https://github.com/UB-Mannheim/tesseract/wiki
    )
)
echo  Deutsche Sprachdateien fuer die Texterkennung werden geladen ...
"%VPY%" -m doc_anonymizer.setup_helper tessdata || goto :error

rem --- 5. Verknuepfung auf dem Desktop -----------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Dokumenten-Anonymisierer.lnk');$s.TargetPath='%~dp0Start.bat';$s.WorkingDirectory='%~dp0';$s.IconLocation='shell32.dll,48';$s.Save()" >nul 2>&1

echo.
echo  ------------------------------------------------------------------
"%VPY%" -m doc_anonymizer check
echo  ------------------------------------------------------------------
echo.
echo  Installation abgeschlossen. Start per Doppelklick auf
echo  "Dokumenten-Anonymisierer" auf dem Desktop oder auf "Start.bat".
echo.
pause
exit /b 0

:error
echo.
echo  [FEHLER] Die Installation ist fehlgeschlagen. Bitte Internetverbindung
echo  pruefen und "Installieren.bat" erneut starten.
pause
exit /b 1
