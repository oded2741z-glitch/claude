[Setup]
AppName=Pulse
AppVersion=1.0.0
AppPublisher=Pulse
DefaultDirName={autopf}\Pulse
DefaultGroupName=Pulse
UninstallDisplayIcon={app}\Pulse.exe
OutputDir=installer
OutputBaseFilename=Pulse-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "hebrew"; MessagesFile: "compiler:Languages\Hebrew.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\Pulse.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\Pulse"; Filename: "{app}\Pulse.exe"; IconFilename: "{app}\Pulse.exe"
Name: "{group}\Uninstall Pulse"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Pulse"; Filename: "{app}\Pulse.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\Pulse.exe"; Description: "Launch Pulse"; Flags: nowait postinstall skipifsilent
