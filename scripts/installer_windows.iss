; ==============================================================================
; Facebook Automation Bot - Inno Setup 6 Script (.exe Installer)
; ==============================================================================
; Produces a single, self-extracting, non-admin installer: FacebookBotSetup.exe
; Installs into %LOCALAPPDATA%\Programs\FacebookBot (User-level, zero UAC required)
; Supports silent auto-updates via /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
; ==============================================================================

#define MyAppName "Facebook Automation Bot"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Facebook Automation Bot Team"
#define MyAppExeName "FacebookBot.exe"

[Setup]
AppId={{E581F782-95A2-4BC2-9B7E-74636BE26891}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\FacebookBot
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist_setup
OutputBaseFilename=FacebookBotSetup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; Non-Admin privilege level: Installs in user-space without UAC admin prompt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline

; Auto-Update Support: Force-close running instance during silent update
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\FacebookBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Do NOT copy user data files (.env, posts.json, links.json) into {app}.
; User data is preserved in %LOCALAPPDATA%\FacebookAutomationBot by the application runtime.

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
