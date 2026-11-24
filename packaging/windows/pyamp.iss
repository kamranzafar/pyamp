; SPDX-License-Identifier: GPL-3.0-or-later
; Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
;
; Inno Setup script for the PyAmp installer. Run by packaging\windows\build.ps1, which
; defines AppVersion, SourceDir (the PyInstaller folder), OutputDir, OutputName, RootDir.
;
; Installs for the current user only (no admin rights): %LOCALAPPDATA%\Programs\PyAmp.

#ifndef AppVersion
  #error Run this through packaging\windows\build.ps1
#endif

[Setup]
; Never change AppId: Windows uses it to recognise upgrades and the uninstaller.
AppId={{6F2B3C1E-8A4D-4C9B-9E51-3D7A2F0B8C64}
AppName=PyAmp
AppVersion={#AppVersion}
AppVerName=PyAmp {#AppVersion}
AppPublisher=Kamran Zafar
AppPublisherURL=https://github.com/kamranzafar/pyamp
AppSupportURL=https://github.com/kamranzafar/pyamp/issues
AppCopyright=Copyright (C) 2026 Kamran Zafar
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\PyAmp
DefaultGroupName=PyAmp
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#RootDir}\packaging\windows\PyAmp.ico
UninstallDisplayIcon={app}\PyAmp.exe
UninstallDisplayName=PyAmp
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Close a running PyAmp before upgrading or uninstalling.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PyAmp"; Filename: "{app}\PyAmp.exe"
Name: "{autodesktop}\PyAmp"; Filename: "{app}\PyAmp.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PyAmp.exe"; Description: "{cm:LaunchProgram,PyAmp}"; Flags: nowait postinstall skipifsilent
