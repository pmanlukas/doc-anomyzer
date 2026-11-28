# Dokumenten-Anonymisierer - installation with one PowerShell line.
#
#   irm https://github.com/pmanlukas/doc-anomyzer/releases/latest/download/install.ps1 | iex
#
# What it does: downloads the same Setup.exe that is offered for a double
# click, compares its SHA-256 checksum with the published one, installs it for
# the current user (no administrator rights) and starts the program.
#
# Why: a file fetched by a browser is marked as "from the internet", and
# Windows then shows the SmartScreen warning for the unsigned setup. A file
# fetched from the command line carries no such mark. The person typing the
# line above decides to trust this download - the checksum only proves that
# the file arrived complete and unchanged, not who made it.
#
# Rules for this file:
#   * ASCII only, no byte order mark. Windows PowerShell 5.1 reads a download
#     without a declared character set as ISO-8859-1; umlauts are therefore
#     written as {ae} {oe} {ue} {ss} and inserted when a text is shown.
#   * Never "exit" in the normal case: the line runs inside the user's own
#     PowerShell window, which would close together with every message.
#
# Switches (environment variables, for administrators and the build test):
#   DOC_ANONYMIZER_QUELLE       other download address (folder with the files)
#   DOC_ANONYMIZER_AUTOMATISCH  "1": no progress window, do not start the
#                               program, end PowerShell with exit code 1 on failure

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # the progress display makes downloads very slow

    $Name = 'Dokumenten-Anonymisierer'
    $SetupName = "$Name-Setup.exe"
    $Source = 'https://github.com/pmanlukas/doc-anomyzer/releases/latest/download'
    if ($env:DOC_ANONYMIZER_QUELLE) { $Source = $env:DOC_ANONYMIZER_QUELLE.TrimEnd('/') }
    $Automatic = $env:DOC_ANONYMIZER_AUTOMATISCH -eq '1'
    $App = Join-Path $env:LOCALAPPDATA "Programs\$Name"
    $NeededGB = 4   # setup (0.6 GB) + installed program (about 2 GB) + reserve

    # Umlauts are created from their character codes, so this file stays pure ASCII.
    $Umlauts = @{ '{ae}' = 0xE4; '{oe}' = 0xF6; '{ue}' = 0xFC; '{ss}' = 0xDF }
    function Text([string]$s) {
        foreach ($key in $Umlauts.Keys) { $s = $s.Replace($key, [string][char]$Umlauts[$key]) }
        $s
    }
    function Say([string]$s) { Write-Host (Text $s) }
    function Fail([string]$s) { throw (Text $s) }

    function Get-File([string]$Url, [string]$Target, [bool]$ShowProgress) {
        # curl.exe is part of Windows 10/11 and shows a progress bar. It does
        # not know the proxy settings of Windows, so PowerShell's own download
        # is the second attempt (and the only one on very old systems).
        $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
        if (Test-Path $curl) {
            $options = @('--fail', '--location', '--retry', '3', '--silent', '--show-error')
            if ($ShowProgress) { $options = @('--fail', '--location', '--retry', '3', '--progress-bar') }
            # Start-Process: the bar is written straight to the window and is
            # not mistaken for an error message by PowerShell.
            $p = Start-Process -FilePath $curl -ArgumentList ($options + @('--output', "`"$Target`"", "`"$Url`"")) `
                -NoNewWindow -Wait -PassThru
            if ($p.ExitCode -eq 0 -and (Test-Path $Target)) { return }
            Remove-Item $Target -Force -ErrorAction SilentlyContinue
        }
        try {
            # Old systems would otherwise try a protocol version GitHub no longer accepts.
            $tls = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            [Net.ServicePointManager]::SecurityProtocol = $tls
        } catch { }   # not allowed or not needed here: the system default is used
        Invoke-WebRequest -Uri $Url -OutFile $Target -UseBasicParsing
    }

    $Work = Join-Path $env:TEMP ("$Name-" + [Guid]::NewGuid().ToString('N'))
    $failed = $false
    try {
        Say ''
        Say "$Name - Einrichtung"
        Say ('=' * 40)

        # ---------------------------------------------------------- checks
        if (-not [Environment]::Is64BitOperatingSystem) {
            Fail 'Dieses Programm ben{oe}tigt ein 64-Bit-Windows (Windows 10 oder 11).'
        }
        if ($Source -notmatch '^https://' -and $Source -notmatch '^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)') {
            Fail 'Die Download-Adresse muss mit https:// beginnen.'
        }
        foreach ($folder in @($env:TEMP, $env:LOCALAPPDATA)) {
            try {
                $free = (Get-PSDrive -Name $folder.Substring(0, 1)).Free
            } catch { $free = $null }   # network path: Windows reports a full disk itself
            if ($null -ne $free -and $free -lt $NeededGB * 1GB) {
                Fail ("Auf Laufwerk $($folder.Substring(0, 1)): sind nur noch $([math]::Round($free / 1GB, 1)) GB " +
                      "frei. Ben{oe}tigt werden etwa $NeededGB GB.")
            }
        }
        New-Item -ItemType Directory -Path $Work -Force | Out-Null
        $Setup = Join-Path $Work $SetupName
        $Sum = "$Setup.sha256"

        # -------------------------------------------------------- download
        Say '1/3  Herunterladen (etwa 590 MB - je nach Leitung einige Minuten) ...'
        try {
            Get-File "$Source/$SetupName.sha256" $Sum $false
            Get-File "$Source/$SetupName" $Setup $true
        } catch {
            Fail ("Der Download ist fehlgeschlagen. Bitte die Internetverbindung pr{ue}fen und die Zeile " +
                  "noch einmal ausf{ue}hren.`n     Adresse: $Source`n     Meldung: $($_.Exception.Message)")
        }

        # -------------------------------------------------------- checksum
        Say '2/3  Pr{ue}fsumme vergleichen ...'
        $expected = ''
        if ((Get-Content -LiteralPath $Sum -Raw) -match '\b([0-9a-fA-F]{64})\b') { $expected = $Matches[1] }
        $actual = (Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash
        if (-not $expected -or $actual -ne $expected) {
            Fail ("Die heruntergeladene Datei stimmt nicht mit der ver{oe}ffentlichten Pr{ue}fsumme {ue}berein " +
                  "- sie wird nicht ausgef{ue}hrt.`n     Bitte die Zeile in einigen Minuten noch einmal ausf{ue}hren.")
        }
        Say "     in Ordnung (SHA-256 $($actual.ToLower().Substring(0, 16))...)"

        # --------------------------------------------------------- install
        Say '3/3  Einrichten (meist 1-2 Minuten, auf {ae}lteren Computern bis zu 10; ohne Administratorrechte) ...'
        # /SILENT shows only the progress window; a running older version is
        # ended by the setup itself. User data is never touched.
        $mode = '/SILENT'
        if ($Automatic) { $mode = '/VERYSILENT' }
        $log = Join-Path $env:TEMP "$Name-Einrichtung.log"
        try {
            $p = Start-Process -FilePath $Setup -Wait -PassThru -ArgumentList `
                $mode, '/CURRENTUSER', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$log`""
        } catch {
            # Typical cause: Smart App Control or a company policy that only
            # allows signed programs.
            Fail ("Windows hat den Start der Einrichtung verhindert:`n     $($_.Exception.Message)`n" +
                  "     Auf diesem Computer d{ue}rfen vermutlich nur signierte Programme gestartet werden " +
                  "(Smart App Control oder eine Firmenrichtlinie).")
        }
        $program = Join-Path $App 'python\pythonw.exe'
        if ($p.ExitCode -ne 0 -or -not (Test-Path $program)) {
            Fail "Die Einrichtung wurde nicht abgeschlossen (Code $($p.ExitCode)).`n     Protokoll: $log"
        }
        Remove-Item $log -Force -ErrorAction SilentlyContinue

        Say ''
        Say 'Fertig. Das Programm ist eingerichtet.'
        Say "Sie starten es k{ue}nftig {ue}ber das Symbol `"$Name`" auf dem Desktop oder im Startmen{ue}."
        if (-not $Automatic) {
            Say 'Es wird jetzt ge{oe}ffnet - dieses Fenster k{oe}nnen Sie schlie{ss}en.'
            Start-Process -FilePath $program -ArgumentList '-m', 'doc_anonymizer.launcher' -WorkingDirectory $App
        }
        Say ''
    } catch {
        $failed = $true
        Write-Host ''
        Write-Host (Text "Die Einrichtung hat nicht geklappt:") -ForegroundColor Red
        Write-Host "     $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ''
    } finally {
        # The setup file is no longer needed (the installed program has its own uninstaller).
        if (Test-Path $Work) { Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if ($failed -and $Automatic) { exit 1 }
}
