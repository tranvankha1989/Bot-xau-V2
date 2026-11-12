; Inno Setup Script - Trading AI PRO by Kha Tran Design
; Phien ban: 1.0.2
; Bo cai dat Setup thuong mai chuyen nghiep - Ma hoa nhi phan 100%

#define MyAppName "Trading AI PRO by Kha Tran Design"
#define MyAppVersion "1.0.2"
#define MyAppPublisher "Kha Tran Design"
#define MyAppExeName "Trading AI PRO.exe"

[Setup]
AppId={{E58C2928-4D6C-4788-B8C1-F0918D68A777}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Trading AI PRO
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin

OutputDir=..\installer_output
OutputBaseFilename=Setup_Trading_AI_PRO_v{#MyAppVersion}
SetupIconFile=license-key-icon.ico
UninstallDisplayIcon={app}\license-key-icon.ico

Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=8
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Dirs]
Name: "{app}"; Permissions: users-full
Name: "{app}\logs"; Permissions: users-full
Name: "{app}\model"; Permissions: users-full

[Files]
; 1. Cac file chuong trinh va Runtime da duoc build boi PyInstaller
Source: "..\build_staging\dist\Trading AI PRO\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 2. Dong co Nhi phan Ma hoa (Chua toan bo thuat toan va ma nguon)
Source: "..\core_engine.bin"; DestDir: "{app}"; Flags: ignoreversion

; 3. Thong tin phien ban va Icon
Source: "..\version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "license-key-icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "run_debug.bat"; DestDir: "{app}"; Flags: ignoreversion

; 4. Trinh cai dat Microsoft Visual C++ 2015-2022 x64 (Tu dong cai dat neu may ao chua co)
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

; 5. Trong so Mo hinh AI chuyen dung cho Vang (Exness)
Source: "..\model\weights\Kronos-Gold-Exness\*"; DestDir: "{app}\model\weights\Kronos-Gold-Exness"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\model\weights\Kronos-Tokenizer-Gold-Exness\*"; DestDir: "{app}\model\weights\Kronos-Tokenizer-Gold-Exness"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\license-key-icon.ico"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\license-key-icon.ico"; Tasks: desktopicon; WorkingDir: "{app}"

[Run]
; Tu dong cai dat VC++ Redistributable trong im lang neu he thong thieu
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /passive /norestart"; Flags: waituntilterminated; Check: VCRedistNeedsInstall
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[Code]
function VCRedistNeedsInstall: Boolean;
var
  Installed: Cardinal;
begin
  // Kiem tra xem may da co Visual C++ 2015-2022 x64 chua qua Registry
  Result := True;
  if RegQueryDWordValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64', 'Installed', Installed) then
  begin
    if Installed = 1 then
      Result := False;
  end;
end;
