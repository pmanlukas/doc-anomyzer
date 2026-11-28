<#
.SYNOPSIS
  Builds the program folder (build\app) and the Windows setup
  (dist\Dokumenten-Anonymisierer-Setup.exe).

.DESCRIPTION
  Runs on GitHub Actions (windows-latest), see .github/workflows/windows-setup.yml.
  Needs: Python 3.12 (64 bit) on PATH (only to install the packages), 7-Zip,
  Inno Setup 6. Everything the program needs ends up in build\app – the
  setup itself downloads nothing.

  build\app
    python\            Python 3.12 "embeddable" + all packages + spaCy model
    doc_anonymizer\    the program
    tesseract\         Tesseract OCR (from the UB Mannheim build)
    models\tessdata\   OCR language files deu/eng/osd (tessdata_best) and
                       deu_fast/eng_fast (tessdata_fast)
    app.ico            program icon
#>
param(
  # Last 3.12 release with an official "embeddable" Windows package.
  [string]$PythonEmbedVersion = "3.12.10",
  [string]$TesseractUrl = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe",
  [string]$SpacyModelUrl = "https://github.com/explosion/spacy-models/releases/download/de_core_news_lg-3.8.0/de_core_news_lg-3.8.0-py3-none-any.whl",
  [switch]$SkipSetup
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is much faster without

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Root "build"
$App = Join-Path $Build "app"
$Downloads = Join-Path $Build "downloads"

function Step([string]$Text) { Write-Host "`n=== $Text ===" }

# SHA-256 of everything that is downloaded and ends up in the setup. A file
# that does not match (tampered with, replaced upstream, cut off) stops the
# build.
$Checksums = @{
  "tesseract-ocr-w64-setup-5.4.0.20240606.exe" = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
  "deu.traineddata"                            = "8407331d6aa0229dc927685c01a7938fc5a641d1a9524f74838cdac599f0d06e"
  "eng.traineddata"                            = "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba"
  "osd.traineddata"                            = "9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff"
  # tessdata_fast 4.1.0, stored under their own names next to the accurate files
  "deu_fast.traineddata"                       = "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d"
  "eng_fast.traineddata"                       = "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
  "de_core_news_lg-3.8.0-py3-none-any.whl"     = "36fda650e476b54d5e87803635e36dadd1e8e034c4b5962088586d684f4c9fed"
}

function Get-Download([string]$Url, [string]$Target) {
  if (-not (Test-Path $Target)) {
    Write-Host "Lade $Url"
    Invoke-WebRequest -Uri $Url -OutFile "$Target.part" -UseBasicParsing
    Move-Item "$Target.part" $Target
  }
  $name = Split-Path $Target -Leaf
  $actual = (Get-FileHash $Target -Algorithm SHA256).Hash.ToLower()
  $expected = $Checksums[$name]
  if ($expected) {
    if ($actual -ne $expected) {
      Remove-Item $Target -Force   # never keep (or cache) a wrong file
      throw "Prüfsumme von $name stimmt nicht: erwartet $expected, erhalten $actual"
    }
    Write-Host "[OK] Prüfsumme $name"
  } else {
    Write-Host "SHA-256 $name = $actual (kein fester Prüfwert – Signatur wird geprüft)"
  }
}

# Programs signed by their publisher (Python): the Windows signature must be
# valid and belong to the expected publisher.
function Assert-Signature([string]$File, [string]$Publisher) {
  $sig = Get-AuthenticodeSignature $File
  if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch [regex]::Escape($Publisher)) {
    throw "Signatur von $(Split-Path $File -Leaf) ungültig oder nicht von '$Publisher' ($($sig.Status); $($sig.SignerCertificate.Subject))"
  }
  Write-Host "[OK] Signatur $(Split-Path $File -Leaf): $Publisher"
}

function Assert-Exit([string]$What) {
  if ($LASTEXITCODE -ne 0) { throw "$What fehlgeschlagen (Exitcode $LASTEXITCODE)" }
}

$Version = (Select-String -Path "$Root\doc_anonymizer\__init__.py" `
  -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Dokumenten-Anonymisierer $Version"

Step "Aufräumen"
if (Test-Path $App) { Remove-Item -Recurse -Force $App }
New-Item -ItemType Directory -Force $App, $Downloads | Out-Null

Step "Python $PythonEmbedVersion (embeddable)"
$embed = Join-Path $Downloads "python-$PythonEmbedVersion-embed-amd64.zip"
Get-Download "https://www.python.org/ftp/python/$PythonEmbedVersion/python-$PythonEmbedVersion-embed-amd64.zip" $embed
Expand-Archive $embed -DestinationPath "$App\python"
foreach ($file in "python.exe", "pythonw.exe", "python312.dll") {
  Assert-Signature "$App\python\$file" "Python Software Foundation"
}
$pth = Get-ChildItem "$App\python\python3*._pth" | Select-Object -First 1
$stdlib = (Get-ChildItem "$App\python\python3*.zip" | Select-Object -First 1).Name
# Search path: standard library, python folder, program folder (..), packages.
Set-Content -Path $pth.FullName -Encoding ascii -Value @($stdlib, ".", "..", "Lib\site-packages", "import site")
New-Item -ItemType Directory -Force "$App\python\Lib\site-packages" | Out-Null

Step "Visual-C++-Laufzeit (neben python.exe, falls auf dem Ziel-PC nicht installiert)"
foreach ($dll in "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll",
                 "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll") {
  $src = Join-Path "$env:SystemRoot\System32" $dll
  if ((Test-Path $src) -and -not (Test-Path "$App\python\$dll")) { Copy-Item $src "$App\python\" }
}

Step "Python-Pakete und Sprachmodell"
$pyver = & python -c "import sys; print(sys.version_info[0], sys.version_info[1], sys.maxsize > 2**32)"
if ($pyver -ne "3 12 True") { throw "Für den Build wird Python 3.12 (64 bit) benötigt, gefunden: $pyver" }
$model = Join-Path $Downloads (Split-Path $SpacyModelUrl -Leaf)
Get-Download $SpacyModelUrl $model
& python -m pip install --disable-pip-version-check --no-warn-script-location --prefer-binary `
  --target "$App\python\Lib\site-packages" -r "$Root\requirements.txt" $model
Assert-Exit "pip install"
# Command line wrappers are not needed (and would point to the build Python).
Remove-Item -Recurse -Force "$App\python\Lib\site-packages\bin" -ErrorAction SilentlyContinue
# Exactly which versions are inside this setup (for later questions and audits).
& python -m pip list --path "$App\python\Lib\site-packages" --format=freeze --disable-pip-version-check |
  Set-Content -Path "$App\enthaltene_pakete.txt" -Encoding utf8
Get-Content "$App\enthaltene_pakete.txt"

Step "Tesseract (aus dem UB-Mannheim-Installer entpackt)"
$tessSetup = Join-Path $Downloads (Split-Path $TesseractUrl -Leaf)
Get-Download $TesseractUrl $tessSetup
$extract = Join-Path $Build "tesseract_extract"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
$sevenZip = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $sevenZip) { throw "7-Zip nicht gefunden" }
& $sevenZip x $tessSetup "-o$extract" -y | Out-Null
Assert-Exit "7-Zip"
New-Item -ItemType Directory -Force "$App\tesseract" | Out-Null
# Without the installer's own files ($PLUGINSDIR, uninstaller).
Get-ChildItem $extract | Where-Object { $_.Name -notlike '$*' -and $_.Name -notlike '*uninst*' } |
  Copy-Item -Destination "$App\tesseract" -Recurse
if (-not (Test-Path "$App\tesseract\tesseract.exe")) { throw "tesseract.exe fehlt nach dem Entpacken" }
& "$App\tesseract\tesseract.exe" --version
Assert-Exit "tesseract --version"

Step "Sprachdateien für die Texterkennung (deu, eng, osd)"
$tessdata = "$App\models\tessdata"
New-Item -ItemType Directory -Force $tessdata | Out-Null
foreach ($lang in "deu", "eng", "osd") {
  $file = Join-Path $Downloads "$lang.traineddata"
  # Fixed release (not the moving "main" branch).
  Get-Download "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/4.1.0/$lang.traineddata" $file
  Copy-Item $file $tessdata
}
# Second, fast set of language files: used for the check of finished text
# pages (crisp page images), where it reads as well in half the time.
foreach ($lang in "deu", "eng") {
  $file = Join-Path $Downloads "${lang}_fast.traineddata"
  Get-Download "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/$lang.traineddata" $file
  Copy-Item $file $tessdata
}
# Configuration files of Tesseract (output formats) next to the language files;
# the installer's own (less accurate) language files are not needed.
foreach ($sub in "configs", "tessconfigs") {
  if (Test-Path "$App\tesseract\tessdata\$sub") { Copy-Item "$App\tesseract\tessdata\$sub" $tessdata -Recurse -Force }
}
Get-ChildItem "$App\tesseract\tessdata" -Recurse -Filter "*.traineddata" -ErrorAction SilentlyContinue | Remove-Item -Force

Step "Programm"
Copy-Item "$Root\doc_anonymizer" "$App\doc_anonymizer" -Recurse
Get-ChildItem "$App\doc_anonymizer" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item "$Root\README.md", "$Root\LICENSE", "$Root\ANLEITUNG.txt" $App
& python "$PSScriptRoot\make_icon.py" "$App\app.ico"
Assert-Exit "Programmsymbol"

Step "Vorkompilieren (schnellerer erster Start)"
# Errors in single files of packages (e.g. test helpers) do not matter.
& "$App\python\python.exe" -m compileall -q -j 0 "$App\doc_anonymizer" "$App\python\Lib\site-packages" | Out-Null

Step "Selbsttest des Programmordners"
& "$App\python\python.exe" -m doc_anonymizer check
Assert-Exit "doc_anonymizer check"
$size = (Get-ChildItem $App -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("Programmordner: {0:N0} MB" -f $size)

if (-not $SkipSetup) {
  Step "Setup.exe (Inno Setup)"
  $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $iscc) { throw "Inno Setup 6 nicht gefunden" }
  & $iscc "/Qp" "/DSourceDir=$App" "/DAppVersion=$Version" "$PSScriptRoot\setup.iss"
  Assert-Exit "Inno Setup"
  $setup = Get-Item "$Root\dist\Dokumenten-Anonymisierer-Setup.exe"
  Write-Host ("Setup: {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
  # Checksum next to the setup: whoever receives the file can compare it
  # (PowerShell: Get-FileHash Dokumenten-Anonymisierer-Setup.exe).
  $hash = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash.ToLower()
  Set-Content -Path "$Root\dist\Dokumenten-Anonymisierer-Setup.exe.sha256" -Encoding ascii `
    -Value "$hash  Dokumenten-Anonymisierer-Setup.exe"
  Write-Host "SHA-256: $hash"
}
