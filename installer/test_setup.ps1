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
& python "$PSScriptRoot\api_smoke_test.py" $port @files
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
Write-Host "`nAlle Setup-Tests bestanden."
