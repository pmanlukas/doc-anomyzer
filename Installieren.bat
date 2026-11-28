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

rem Programmordner darf nicht in einem ZIP-Archiv liegen.
echo "%~dp0" | findstr /i /c:".zip\\" >nul && (
    echo  [FEHLER] Bitte die ZIP-Datei zuerst entpacken:
    echo  Rechtsklick auf die ZIP-Datei - "Alle extrahieren..." - danach im
    echo  entpackten Ordner "Installieren.bat" starten.
    pause
    exit /b 1
)

set "PY_URL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
set "TESS_URL=https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
rem Pruefsumme (SHA-256) des Tesseract-Installationsprogramms: nur genau diese
rem Datei wird ausgefuehrt.
set "TESS_SHA256=c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
rem Eigener, neuer Ordner fuer die Downloads (kein vorhersehbarer Name).
set "DL=%TEMP%\doc_anonymizer_setup_%RANDOM%%RANDOM%"
mkdir "%DL%" || goto :error

rem --- 1. Python suchen (3.10 bis 3.12) -----------------------------------
call :find_python
if not defined PY (
    echo  Python 3.12 wird installiert ...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    call :find_python
)
if not defined PY (
    rem winget fehlt oder ist gesperrt: Installationsprogramm direkt laden.
    echo  Python wird von python.org heruntergeladen ...
    call :download "%PY_URL%" "%DL%\python-setup.exe" || goto :no_python
    rem Nur ausfuehren, wenn die Datei gueltig von der Python Software Foundation signiert ist.
    call :signed_by "%DL%\python-setup.exe" "Python Software Foundation" || goto :no_python
    echo  Python wird installiert ^(ca. 1-2 Minuten^) ...
    "%DL%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
    call :find_python
)
if not defined PY goto :no_python
echo  [OK] Python gefunden: %PY%

rem --- 2. Eigene Programmumgebung anlegen ---------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  Programmumgebung wird angelegt ...
    %PY% -m venv .venv || goto :error
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check || goto :error
echo  Programmteile werden installiert (dauert einige Minuten) ...
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check || goto :error

rem --- 3. Deutsches Sprachmodell -----------------------------------------
"%VPY%" -c "import de_core_news_lg" >nul 2>&1
if errorlevel 1 (
    echo  Deutsches Sprachmodell wird geladen ^(ca. 550 MB^) ...
    "%VPY%" -m spacy download de_core_news_lg || goto :error
)

rem --- 4. Texterkennung Tesseract ----------------------------------------
call :has_tesseract
if errorlevel 1 (
    echo  Texterkennung Tesseract wird installiert ...
    winget install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
    call :has_tesseract
)
if errorlevel 1 (
    echo  Tesseract wird von GitHub heruntergeladen ...
    call :install_tesseract
    call :has_tesseract
)
if errorlevel 1 (
    echo  [HINWEIS] Die Texterkennung konnte nicht installiert werden. Digitale PDFs
    echo  funktionieren trotzdem. Fuer eingescannte PDFs bitte Tesseract installieren:
    echo  https://github.com/UB-Mannheim/tesseract/wiki
)
echo  Deutsche Sprachdateien fuer die Texterkennung werden geladen ...
"%VPY%" -m doc_anonymizer.setup_helper tessdata
if errorlevel 1 (
    echo  [HINWEIS] Die deutschen Sprachdateien konnten nicht geladen werden.
    echo  Digitale PDFs funktionieren trotzdem. Fuer Scans bitte spaeter
    echo  "Installieren.bat" noch einmal starten.
)

rem --- 5. Verknuepfung auf dem Desktop -----------------------------------
rem (Ordner ueber eine Umgebungsvariable: Sonderzeichen im Pfad stoeren so nicht.)
set "APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Dokumenten-Anonymisierer.lnk');$s.TargetPath=$env:APP_DIR+'Start.bat';$s.WorkingDirectory=$env:APP_DIR;$s.IconLocation='shell32.dll,48';$s.Save()" >nul 2>&1

echo.
echo  ------------------------------------------------------------------
"%VPY%" -m doc_anonymizer check
echo  ------------------------------------------------------------------
echo.
echo  Installation abgeschlossen. Start per Doppelklick auf
echo  "Dokumenten-Anonymisierer" auf dem Desktop oder auf "Start.bat".
echo.
rd /s /q "%DL%" >nul 2>&1
pause
exit /b 0

rem ======================================================================
:find_python
rem Sucht Python 3.10-3.12: py-Starter, PATH und die Standard-Installationsorte
rem (nach einer Neuinstallation ist der PATH in diesem Fenster noch alt).
set "PY="
for %%V in (3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>&1 && set "PY=python"
)
for %%D in (Python312 Python311 Python310) do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\%%D\python.exe""
    if not defined PY if exist "%ProgramFiles%\%%D\python.exe" set "PY="%ProgramFiles%\%%D\python.exe""
)
exit /b 0

:has_tesseract
"%VPY%" -c "from doc_anonymizer import ocr; import sys; sys.exit(0 if ocr.is_available() else 1)" >nul 2>&1
exit /b %errorlevel%

:download
rem %1 = Adresse, %2 = Zieldatei. Eine schon vorhandene Datei wird nie benutzt;
rem schlaegt der Download fehl, bleibt keine Datei zurueck.
set "DL_URL=%~1"
set "DL_OUT=%~2"
if exist "%DL_OUT%" del /f /q "%DL_OUT%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol='Tls12'; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri $env:DL_URL -OutFile $env:DL_OUT" >nul 2>&1
if errorlevel 1 (
    if exist "%DL_OUT%" del /f /q "%DL_OUT%" >nul 2>&1
    exit /b 1
)
if not exist "%DL_OUT%" exit /b 1
exit /b 0

:signed_by
rem %1 = Datei, %2 = erwarteter Herausgeber der Windows-Signatur
set "CHK_FILE=%~1"
set "CHK_PUBLISHER=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=Get-AuthenticodeSignature -LiteralPath $env:CHK_FILE; if($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -like ('*'+$env:CHK_PUBLISHER+'*')){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo  [FEHLER] Die heruntergeladene Datei ist nicht gueltig signiert und wird
    echo  nicht ausgefuehrt.
    del /f /q "%CHK_FILE%" >nul 2>&1
    exit /b 1
)
exit /b 0

:install_tesseract
rem Laedt das Installationsprogramm, prueft die Pruefsumme und startet es erst dann
rem (mit Administratorrechten - Windows fragt nach).
set "TESS_SETUP=%DL%\tesseract-setup.exe"
call :download "%TESS_URL%" "%TESS_SETUP%" || exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if((Get-FileHash -LiteralPath $env:TESS_SETUP -Algorithm SHA256).Hash -eq $env:TESS_SHA256){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo  [HINWEIS] Die Pruefsumme des Tesseract-Downloads stimmt nicht - die Datei
    echo  wird nicht ausgefuehrt.
    del /f /q "%TESS_SETUP%" >nul 2>&1
    exit /b 1
)
echo  Bitte die folgende Windows-Abfrage mit "Ja" bestaetigen.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath $env:TESS_SETUP -ArgumentList '/S' -Verb RunAs -Wait" >nul 2>&1
exit /b 0

:no_python
echo.
echo  [FEHLER] Python konnte nicht automatisch installiert werden.
echo  Bitte Python 3.12 von https://www.python.org/downloads/ installieren
echo  ^(Haken bei "Add python.exe to PATH" setzen^) und diese Datei erneut starten.
pause
exit /b 1

:error
echo.
echo  [FEHLER] Die Installation ist fehlgeschlagen. Bitte Internetverbindung
echo  pruefen und "Installieren.bat" erneut starten.
pause
exit /b 1
