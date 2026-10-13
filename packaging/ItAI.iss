[Setup]
AppId={{4E12C36D-FB9A-49DC-B667-2A86848DF345}
AppName=ItAI
AppVersion=3.1.0
AppPublisher=ItAI
AppPublisherURL=https://github.com/Rai07007/ItAI
DefaultDirName={localappdata}\Programs\ItAI
DefaultGroupName=ItAI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist-installer
OutputBaseFilename=ItAI-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ItAI.exe
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "..\dist\ItAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ItAI"; Filename: "{app}\ItAI.exe"
Name: "{userdesktop}\ItAI"; Filename: "{app}\ItAI.exe"

[Run]
Filename: "{app}\ItAI.exe"; Description: "Abrir ItAI"; Flags: nowait postinstall skipifsilent
; User measurements live in LOCALAPPDATA\ItAI, outside {app}.
; Intentionally no UninstallDelete entry: uninstalling preserves measurements.
