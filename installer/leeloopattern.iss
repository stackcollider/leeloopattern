; Inno Setup script of the LeeLooPattern installer. Built by installer/build.ps1 (ISCC /DAppVersion=<VERSION>).
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "LeeLooPattern"
#define AppExe "LeeLooPattern.exe"

[Setup]
AppId={{B6E2A1F4-7C3D-4F0A-9A52-5D1E0C7B8A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=StackCollider
DefaultDirName={autopf}\LeeLoo\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=LeeLooPatternSetup-{#AppVersion}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\patterndxf\ui\icons\app.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; the program files of the version installed before: what the new one no longer has must not stay (1.19.5)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\build\dist\LeeLooPattern\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// An older setup over a newer program is refused (1.19.5): the version installed is in the uninstall key Inno wrote.
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B6E2A1F4-7C3D-4F0A-9A52-5D1E0C7B8A31}_is1';

function NextPart(var S: String): Integer;
var
  P: Integer;
begin
  P := Pos('.', S);
  if P = 0 then begin
    Result := StrToIntDef(S, 0);
    S := '';
  end else begin
    Result := StrToIntDef(Copy(S, 1, P - 1), 0);
    Delete(S, 1, P);
  end;
end;

// < 0: A is older than B, 0: the same, > 0: A is newer ("1.19.4" against "1.9.10": 19 is compared with 9, not as text)
function CompareVersions(A, B: String): Integer;
var
  I, X, Y: Integer;
begin
  Result := 0;
  for I := 1 to 3 do begin
    X := NextPart(A);
    Y := NextPart(B);
    if X <> Y then begin
      Result := X - Y;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
var
  Installed: String;
begin
  Result := True;
  if RegQueryStringValue(HKA, UninstallKey, 'DisplayVersion', Installed) and
     (CompareVersions(Installed, '{#AppVersion}') > 0) then begin
    SuppressibleMsgBox('{#AppName} ' + Installed + ' is installed, this setup has the older {#AppVersion}.' + #13#10 +
      'To go back to {#AppVersion}, uninstall ' + Installed + ' first (Settings > Apps).', mbError, MB_OK, IDOK);
    Log('Refused: installed ' + Installed + ' is newer than {#AppVersion}');
    Result := False;
  end;
end;
