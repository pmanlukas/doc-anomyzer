; Dokumenten-Anonymisierer – Windows-Setup (Inno Setup 6)
;
; Built by installer/build_windows.ps1 (GitHub Actions, windows-latest):
;   ISCC.exe /DSourceDir=<build\app> /DAppVersion=<x.y.z> installer\setup.iss
;
; * completely offline: Python, packages, language model, Tesseract and the
;   OCR language files are inside the setup – nothing is downloaded,
; * no administrator rights: installs to %LOCALAPPDATA%\Programs,
; * user data (settings, learning progress, results) lives elsewhere
;   (%LOCALAPPDATA%\DocAnonymizer, %USERPROFILE%\Anonymisiert) and survives
;   updates and uninstallation. An update = run a newer setup.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\app"
#endif
#define AppName "Dokumenten-Anonymisierer"

[Setup]
; Fixed ID: a newer setup recognises and updates the existing installation.
AppId={{6B2F4C1E-8D3A-4F7B-9E21-5C0A7D93B184}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppComments=Anonymisiert PDF-Dokumente vollständig auf diesem Computer.
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
; As few pages as possible: no folder, no start menu folder, no welcome page.
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableReadyMemo=yes
; No administrator rights needed (no UAC prompt).
PrivilegesRequired=lowest
UsedUserAreasWarning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Dokumenten-Anonymisierer-Setup
SetupIconFile={#SourceDir}\app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
ShowLanguageDialog=no
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
; A running instance is ended before files are replaced (see [Code]);
; the restart manager is the fallback.
CloseApplications=force
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
de.ReadyLabel1=Der Dokumenten-Anonymisierer wird jetzt auf diesem Computer eingerichtet.
de.ReadyLabel2a=Klicken Sie auf „Installieren“. Es wird nichts aus dem Internet geladen und es werden keine Administratorrechte benötigt.
de.FinishedLabel=Der Dokumenten-Anonymisierer ist eingerichtet. Sie starten ihn jederzeit über das Symbol „Dokumenten-Anonymisierer“ auf dem Desktop.

[InstallDelete]
; Updates: remove the previous program files completely (no stale modules).
; User data is not stored here.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\doc_anonymizer"
Type: filesandordirs; Name: "{app}\tesseract"
Type: filesandordirs; Name: "{app}\models"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m doc_anonymizer.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Comment: "PDF-Dokumente anonymisieren"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m doc_anonymizer.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Comment: "PDF-Dokumente anonymisieren"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m doc_anonymizer.launcher"; WorkingDir: "{app}"; Description: "Programm jetzt starten"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; End a running instance before its files are removed.
Filename: "{app}\python\python.exe"; Parameters: "-m doc_anonymizer.launcher --beenden"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "ProgrammBeenden"

[UninstallDelete]
; Files created at runtime inside the program folder (compiled Python files).
Type: filesandordirs; Name: "{app}"

[Code]
// Update while the program is running: end it first (it holds DLLs open).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Python: String;
begin
  Result := '';
  Python := ExpandConstant('{app}\python\python.exe');
  if FileExists(Python) then
    Exec(Python, '-m doc_anonymizer.launcher --beenden', ExpandConstant('{app}'),
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
