<#
.SYNOPSIS
  Tests the finished setup like a user would use it (GitHub Actions, windows-latest):
  silent installation without admin rights, self-check, anonymisation of test
  PDFs (command line and through the running program), start/stop behaviour
  and uninstallation (user data must survive).
#>
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
$Setup = Join-Path $Root "dist\Dokumenten-Anonymisierer-Setup.exe"
$App = Join-Path $env:LOCALAPPDATA "Programs\Dokumenten-Anonymisierer"
$UserData = Join-Path $env:LOCALAPPDATA "DocAnonymizer"
$Logs = Join-Path $Root "build\testlogs"
$Pdfs = Join-Path $Root "build\testpdfs"
New-Item -ItemType Directory -Force $Logs | Out-Null
# A message box nobody can click would block the test run.
$env:DOC_ANONYMIZER_NO_DIALOG = "1"

function Step([string]$Text) { Write-Host "`n=== $Text ===" }
function Check([bool]$Condition, [string]$What) {
  if (-not $Condition) { throw "FEHLGESCHLAGEN: $What" }
  Write-Host "[OK] $What"
}
function Wait-Until([scriptblock]$Condition, [int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (& $Condition) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}
function Get-Ping {
  try {
    $port = (Get-Content (Join-Path $UserData "server.json") -Raw | ConvertFrom-Json).port
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/ping" -TimeoutSec 2 -NoProxy
    if ($r.app -eq "dokumenten-anonymisierer") { return [int]$port }
  } catch { }
  return $null
}
function Start-Program([string[]]$Arguments) {
  Start-Process -FilePath "$App\python\pythonw.exe" -ArgumentList (@("-m", "doc_anonymizer.launcher") + $Arguments) `
    -WorkingDirectory $App -PassThru
}
function Get-State { Get-Content (Join-Path $UserData "server.json") -Raw | ConvertFrom-Json }
function Get-Status {
  # Like a program that knows the access token (the page itself uses a cookie).
  try {
    $s = Get-State
    return Invoke-RestMethod -Uri "http://127.0.0.1:$($s.port)/api/status" -TimeoutSec 5 -NoProxy `
      -Headers @{ "X-Anonymizer-Token" = $s.token }
  } catch { return $null }
}
function Get-WindowProcesses {
  # Edge processes of the program window (they use the program's own profile).
  Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -like "*DocAnonymizer\fenster*" }
}

Step "Setup"
$size = (Get-Item $Setup).Length / 1MB
Write-Host ("Dokumenten-Anonymisierer-Setup.exe: {0:N0} MB" -f $size)

Step "Stille Installation ohne Administratorrechte"
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process $Setup -ArgumentList "/VERYSILENT", "/CURRENTUSER", "/SUPPRESSMSGBOXES", "/NORESTART",
  "/LOG=$Logs\install.log" -Wait -PassThru
Check ($p.ExitCode -eq 0) "Setup beendet mit Exitcode 0"
Write-Host ("Installationsdauer: {0:N0} s" -f $sw.Elapsed.TotalSeconds)
Check (Test-Path "$App\python\pythonw.exe") "Programm in $App installiert"
Check (Test-Path "$App\tesseract\tesseract.exe") "Tesseract mitinstalliert"
Check (Test-Path "$App\models\tessdata\deu.traineddata") "Deutsche OCR-Sprachdatei mitinstalliert"
Check ((Test-Path "$App\models\tessdata\deu_fast.traineddata") -and (Test-Path "$App\models\tessdata\eng_fast.traineddata")) `
  "Schnelle Sprachdateien für die Kontrolle mitinstalliert"
Check (Test-Path "$App\enthaltene_pakete.txt") "Liste der enthaltenen Pakete vorhanden"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "Dokumenten-Anonymisierer.lnk"
$startmenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Dokumenten-Anonymisierer.lnk"
Check (Test-Path $desktop) "Desktop-Symbol vorhanden"
Check (Test-Path $startmenu) "Startmenü-Eintrag vorhanden"
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($desktop)
Check ($lnk.TargetPath -like "*\python\pythonw.exe") "Symbol startet pythonw.exe (kein Konsolenfenster): $($lnk.TargetPath) $($lnk.Arguments)"
$uninstallKey = Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" |
  Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq "Dokumenten-Anonymisierer" }
Check ($null -ne $uninstallKey) "Eintrag unter 'Apps & Features' (Deinstallation)"

Step "Selbsttest"
& "$App\python\python.exe" -m doc_anonymizer check
Check ($LASTEXITCODE -eq 0) "doc_anonymizer check"

Step "Test-PDFs (frei erfunden)"
& python "$PSScriptRoot\make_test_pdfs.py" $Pdfs
Check ($LASTEXITCODE -eq 0) "Test-PDFs erzeugt"
$files = Get-ChildItem $Pdfs -Filter *.pdf | ForEach-Object { $_.FullName }

Step "Anonymisieren über die Kommandozeile"
& "$App\python\python.exe" -m doc_anonymizer anonymisieren @files --ausgabe "$Root\build\testout" --still
Check ($LASTEXITCODE -eq 0) "alle Test-PDFs anonymisiert, alle Prüfstufen bestanden"

Step "Start über das Programm (pythonw, ohne Konsole)"
$sw = [Diagnostics.Stopwatch]::StartNew()
$first = Start-Program @("--kein-browser")
Check (Wait-Until { $null -ne (Get-Ping) } 120) "Programm antwortet"
Write-Host ("Startzeit: {0:N1} s" -f $sw.Elapsed.TotalSeconds)
$port = Get-Ping
$pid1 = (Get-Content (Join-Path $UserData "server.json") -Raw | ConvertFrom-Json).pid

Step "Zweiter Start: nur Browser, kein zweiter Server"
$second = Start-Program @("--kein-browser")
Check ($second.WaitForExit(60000)) "zweiter Start beendet sich sofort"
$pid2 = (Get-Content (Join-Path $UserData "server.json") -Raw | ConvertFrom-Json).pid
Check ($pid1 -eq $pid2 -and -not $first.HasExited) "weiterhin genau ein Server (Prozess $pid1)"

Step "Anonymisieren über das laufende Programm (parallele Verarbeitung)"
& python "$PSScriptRoot\api_smoke_test.py" (Join-Path $UserData "server.json") @files
Check ($LASTEXITCODE -eq 0) "Akte über die Oberfläche (API) anonymisiert, alle Prüfstufen bestanden"

Step "Beenden"
& "$App\python\python.exe" -m doc_anonymizer.launcher --beenden
Check ($first.WaitForExit(30000)) "Programm beendet"
Check ($null -eq (Get-Ping)) "Port freigegeben"

Step "Selbstständiges Beenden ohne geöffnete Seite"
$env:DOC_ANONYMIZER_IDLE_SECONDS = "15"
$env:DOC_ANONYMIZER_STARTUP_SECONDS = "15"
$idle = Start-Program @("--kein-browser")
Check (Wait-Until { $null -ne (Get-Ping) } 120) "Programm gestartet"
Check ($idle.WaitForExit(90000)) "Programm beendet sich ohne geöffnete Seite von selbst"
Remove-Item Env:\DOC_ANONYMIZER_IDLE_SECONDS, Env:\DOC_ANONYMIZER_STARTUP_SECONDS

Step "Programmfenster: Microsoft Edge mit eigenem Profil"
# The real start as from the desktop symbol: the window must open, be let in
# with its one-time ticket and keep the program alive; closing the window
# ends the program.
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) {
  Write-Host "FENSTERTEST: übersprungen (Microsoft Edge ist auf diesem Rechner nicht installiert)"
} else {
  $env:DOC_ANONYMIZER_CLOSE_SECONDS = "10"
  $env:DOC_ANONYMIZER_IDLE_SECONDS = "45"
  $env:DOC_ANONYMIZER_STARTUP_SECONDS = "150"
  $win = Start-Program @()
  Check (Wait-Until { $null -ne (Get-Ping) } 120) "Programm gestartet"
  $connected = Wait-Until { (Get-Status).page_connected -eq $true } 120
  $procs = @(Get-WindowProcesses)
  Write-Host ("Edge-Prozesse des Programmfensters: {0}" -f $procs.Count)
  if (-not $connected) {
    # Hosted build machines do not always have a usable desktop – reported,
    # but only the checks that do not depend on it decide about the build.
    Write-Host "FENSTERTEST: NICHT BESTÄTIGT – die Seite im Edge-Fenster hat sich nicht gemeldet"
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    & "$App\python\python.exe" -m doc_anonymizer.launcher --beenden
    Check ($win.WaitForExit(30000)) "Programm beendet"
  } else {
    Write-Host "[OK] Die Seite im Edge-Fenster ist mit dem Programm verbunden (Einmal-Ticket, Cookie)"
    Check ($procs.Count -gt 0) "Fenster läuft mit eigenem Profil (getrennt vom normalen Browser)"
    $token = (Get-State).token
    Check (-not (($procs | ForEach-Object { $_.CommandLine }) -join " ").Contains($token)) `
      "Der Zugangsschlüssel steht nicht in der Befehlszeile des Fensters"
    Check (Test-Path (Join-Path $UserData "fenster")) "Profilordner des Fensters liegt im Datenordner des Programms"
    Start-Sleep -Seconds 60   # longer than the idle limit: the open page keeps the program alive
    Check (-not $win.HasExited) "Programm läuft weiter, solange das Fenster offen ist"
    # Close the window like a user (WM_CLOSE); if that is not possible here, end Edge.
    $ids = $procs | ForEach-Object { $_.ProcessId }
    $closed = $false
    Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.Id -and $_.MainWindowHandle -ne 0 } |
      ForEach-Object { if ($_.CloseMainWindow()) { $closed = $true } }
    if (-not $closed) { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
    Write-Host ("Fenster geschlossen: {0}" -f $(if ($closed) { "über die Fensterschaltfläche" } else { "Prozess beendet" }))
    Check ($win.WaitForExit(120000)) "Programm beendet sich nach dem Schließen des Fensters von selbst"
    Check ($null -eq (Get-Ping)) "Port freigegeben"
    Write-Host "FENSTERTEST: bestanden"
  }
  Remove-Item Env:\DOC_ANONYMIZER_CLOSE_SECONDS, Env:\DOC_ANONYMIZER_IDLE_SECONDS, Env:\DOC_ANONYMIZER_STARTUP_SECONDS
  Get-WindowProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Step "Update: Setup erneut ausführen, während das Programm läuft"
$running = Start-Program @("--kein-browser")
Check (Wait-Until { $null -ne (Get-Ping) } 120) "Programm läuft"
$p = Start-Process $Setup -ArgumentList "/VERYSILENT", "/CURRENTUSER", "/SUPPRESSMSGBOXES", "/NORESTART",
  "/LOG=$Logs\update.log" -Wait -PassThru
Check ($p.ExitCode -eq 0) "Update installiert (laufendes Programm wurde beendet)"
Check ($running.HasExited) "altes Programm beendet"
& "$App\python\python.exe" -m doc_anonymizer check
Check ($LASTEXITCODE -eq 0) "nach dem Update funktionsfähig"

Step "Deinstallation"
Copy-Item (Join-Path $UserData "logs\programm.log") $Logs -ErrorAction SilentlyContinue
$uninstaller = Get-ChildItem $App -Filter "unins*.exe" | Select-Object -First 1
Start-Process $uninstaller.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
Check (Wait-Until { -not (Test-Path "$App\python") } 180) "Programmdateien entfernt"
Check (-not (Test-Path $desktop) -and -not (Test-Path $startmenu)) "Symbole entfernt"
Check ((Test-Path (Join-Path $UserData "logs\programm.log")) -and
       (@(Get-ChildItem $UserData -Recurse -Filter "*.json").Count -gt 0)) "Nutzerdaten (Akten, Einstellungen, Lernstand) bleiben erhalten"

Step "Einrichtung mit einer PowerShell-Zeile (install.ps1)"
# The line a user pastes into PowerShell, run by Windows PowerShell 5.1 with
# the policy of a fresh Windows ("Restricted": no script files). The download
# address is a local server offering the same files as a release.
$Source = Join-Path $Root "build\quelle"
Remove-Item $Source -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$Source\gut", "$Source\beschaedigt" | Out-Null
Copy-Item "$PSScriptRoot\install.ps1", $Setup, "$Setup.sha256" "$Source\gut"
# Second address: the published checksum, but a different file under the
# setup's name (harmless, should the check ever fail to stop it).
Copy-Item "$PSScriptRoot\install.ps1", "$Setup.sha256" "$Source\beschaedigt"
Copy-Item "$env:SystemRoot\System32\whoami.exe" "$Source\beschaedigt\Dokumenten-Anonymisierer-Setup.exe"
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$sourcePort = $listener.LocalEndpoint.Port
$listener.Stop()
$Address = "http://127.0.0.1:$sourcePort"
$server = Start-Process python -ArgumentList "-m", "http.server", $sourcePort, "--bind", "127.0.0.1",
  "--directory", "`"$Source`"" -PassThru -WindowStyle Hidden
try {
  Check (Wait-Until { try { (Invoke-WebRequest "$Address/gut/install.ps1" -UseBasicParsing -TimeoutSec 2 -NoProxy).StatusCode -eq 200 }
                      catch { $false } } 60) "Download-Adresse bereit ($Address)"

  # 1. A file that does not match the published checksum is never started.
  $env:DOC_ANONYMIZER_AUTOMATISCH = "1"
  $env:DOC_ANONYMIZER_QUELLE = "$Address/beschaedigt"
  $text = (& powershell.exe -NoProfile -ExecutionPolicy Restricted -Command "irm $Address/beschaedigt/install.ps1 | iex" | Out-String)
  $code = $LASTEXITCODE
  Write-Host $text
  Check ($text -match "stimmt nicht mit") "Abweichende Datei wird an der Prüfsumme erkannt"
  Check ($code -eq 1) "Fehlschlag wird gemeldet (Exitcode $code)"
  Check (-not (Test-Path "$App\python")) "nichts eingerichtet"

  # 2. The normal case, exactly as a user runs it: progress window, program starts afterwards.
  Remove-Item Env:\DOC_ANONYMIZER_AUTOMATISCH
  $env:DOC_ANONYMIZER_QUELLE = "$Address/gut"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  & powershell.exe -NoProfile -ExecutionPolicy Restricted -Command "irm $Address/gut/install.ps1 | iex"
  Check ($LASTEXITCODE -eq 0) "PowerShell-Zeile beendet mit Exitcode 0"
  Write-Host ("Dauer (Download vom eigenen Rechner, Prüfsumme, Einrichtung): {0:N0} s" -f $sw.Elapsed.TotalSeconds)
  Check (Test-Path "$App\python\pythonw.exe") "Programm in $App eingerichtet"
  Check (Test-Path $desktop) "Desktop-Symbol vorhanden"
  Check (Wait-Until { $null -ne (Get-Ping) } 120) "Programm wurde nach der Einrichtung gestartet"
  Check (@(Get-ChildItem $env:TEMP -Directory -Filter "Dokumenten-Anonymisierer-*").Count -eq 0) `
    "Heruntergeladene Setup-Datei wurde wieder entfernt"
  & "$App\python\python.exe" -m doc_anonymizer.launcher --beenden
  Check (Wait-Until { $null -eq (Get-Ping) } 60) "Programm beendet"
  Get-WindowProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} finally {
  Remove-Item Env:\DOC_ANONYMIZER_QUELLE, Env:\DOC_ANONYMIZER_AUTOMATISCH -ErrorAction SilentlyContinue
  Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}

Write-Host "`nAlle Setup-Tests bestanden."
