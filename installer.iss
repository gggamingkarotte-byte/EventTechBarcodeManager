#define AppName "EventTech Barcode Manager"
#define AppVersion "1.1.0"
#define AppExeName "EventTechBarcodeManager.exe"
#ifndef AppSourceDir
  #define AppSourceDir "dist\EventTechBarcodeManager"
#endif

[Setup]
AppId={{A164D59B-5120-4F39-B2A2-9A1E2FDD7001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=EventTech
DefaultDirName={autopf}\EventTechBarcodeManager
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=dist\installer
OutputBaseFilename=EventTechBarcodeManager-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
CloseApplications=force
RestartApplications=no
Uninstallable=yes

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktopverknuepfung erstellen"; GroupDescription: "Weitere Aufgaben:"; Flags: unchecked

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "BEDIENUNGSANLEITUNG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "supabase\*"; DestDir: "{app}\supabase"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} starten"; Flags: postinstall nowait skipifsilent
