; 考研学习链 (Kaoyan Study Chain) · Inno Setup 自动化安装向导脚本
#define MyAppName "考研学习链"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "Kaoyan Study Chain Community"
#define MyAppURL "https://github.com/moyetian/kaoyan_chain"
#define MyAppExeName "KaoyanStudyChain.exe"

#if FileExists("KaoyanStudyChain\KaoyanStudyChain.exe")
  #define AppSourceDir "KaoyanStudyChain"
  #define AppIconFile "KaoyanStudyChain\docs\assets\logo\favicon.ico"
  #define MyOutputDir "installer"
#else
  #define AppSourceDir "dist\KaoyanStudyChain"
  #define AppIconFile "docs\assets\logo\favicon.ico"
  #define MyOutputDir "dist\installer"
#endif

[Setup]
AppId={{C897B762-81D2-4820-9D7D-68B9E780E24D}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 允许普通考生安装至个人目录，无需管理员 UAC 强行提权
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir={#MyOutputDir}
OutputBaseFilename=KaoyanStudyChain_Setup_v{#MyAppVersion}
SetupIconFile={#AppIconFile}
UninstallDisplayIcon={app}\docs\assets\logo\favicon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
chinesesimplified.CreateDesktopIcon=创建桌面快捷方式(&D)
chinesesimplified.AdditionalIcons=附加图标:
chinesesimplified.LaunchProgram=立即启动 考研学习链

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 递归打包独立运行包全部内容
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\docs\assets\logo\favicon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\docs\assets\logo\favicon.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent
