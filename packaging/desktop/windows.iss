; Inno Setup script for the Windows installer. Built by .github/workflows/desktop.yml:
;   iscc /DAppVersion=0.1.1 packaging\desktop\windows.iss
; Installs for the current user (no admin rights needed) into %LOCALAPPDATA%\Programs.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{E6F4D03E-2851-4350-B5C4-348ABB6D15CF}
AppName=LeadCommons
AppVersion={#AppVersion}
AppVerName=LeadCommons {#AppVersion}
AppPublisher=LeadCommons contributors
AppPublisherURL=https://github.com/aymaneprojects/leadcommons
AppSupportURL=https://github.com/aymaneprojects/leadcommons/issues
DefaultDirName={autopf}\LeadCommons
DefaultGroupName=LeadCommons
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist-desktop
OutputBaseFilename=LeadCommons-{#AppVersion}-windows-x64-setup
SetupIconFile=icons\leadcommons.ico
UninstallDisplayIcon={app}\LeadCommons.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\LeadCommons\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LeadCommons"; Filename: "{app}\LeadCommons.exe"
Name: "{group}\{cm:UninstallProgram,LeadCommons}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LeadCommons"; Filename: "{app}\LeadCommons.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LeadCommons.exe"; Description: "{cm:LaunchProgram,LeadCommons}"; Flags: nowait postinstall skipifsilent

; Your data (~\.leadcommons) is kept when you uninstall, so a reinstall finds it again.
