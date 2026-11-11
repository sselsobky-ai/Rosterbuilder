; Roster Builder - Inno Setup 6 installer script.
;
; Build:  roster_builder\packaging\build_exe.bat does everything (PyInstaller, then ISCC with /DAppVersion=<runtime.__version__>).
;         By hand:  ISCC.exe /DAppVersion=1.0.0 roster_builder\packaging\RosterBuilder.iss
; Needs:  Inno Setup 6 (the Arabic setup language is added automatically when the installed Inno Setup ships Languages\Arabic.isl).  Download: https://jrsoftware.org/isdl.php
;         or  winget install --id JRSoftware.InnoSetup -e
; Input:  ..\..\dist\RosterBuilder\   (made by PyInstaller from RosterBuilder.spec; contains RosterBuilder.exe)
; Output: ..\..\dist\installer\RosterBuilder_Setup_<version>.exe
; All relative paths are relative to THIS script's folder (roster_builder\packaging).
;
; Per-user install (no administrator rights).  The user's data (%APPDATA%\RosterBuilder: roster.db, backups, log)
; is NOT touched by the installer or the uninstaller; the uninstall prompt says so.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "RosterBuilder.exe"

[Setup]
AppId={{8C5E5C4A-3B0B-4D9B-9E58-5B7A1D6A2F01}
AppName=Roster Builder
AppVersion={#AppVersion}
AppVerName=Roster Builder {#AppVersion}
AppPublisher=Roster Builder
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\RosterBuilder
DefaultGroupName=Roster Builder
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
#if Ver >= EncodeVer(6,3,0)
ArchitecturesInstallIn64BitMode=x64compatible
#else
ArchitecturesInstallIn64BitMode=x64
#endif
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName=Roster Builder
OutputDir=..\..\dist\installer
OutputBaseFilename=RosterBuilder_Setup_{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UsePreviousLanguage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#define HaveArabic
#endif

[CustomMessages]
english.LaunchApp=Launch Roster Builder
english.KeepDataQuestion=Roster Builder will be removed from this computer.%n%nYour data (staff, shifts, rosters and backups) is NOT deleted. It stays in:%n%n%1%n%nContinue with the uninstall?
#ifdef HaveArabic
arabic.LaunchApp=تشغيل Roster Builder
arabic.KeepDataQuestion=ستتم إزالة Roster Builder من هذا الحاسوب.%n%nلن تُحذف بياناتك (الموظفون والمناوبات والجداول والنسخ الاحتياطية). ستبقى في المجلد:%n%n%1%n%nهل تريد متابعة إلغاء التثبيت؟
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\RosterBuilder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Roster Builder"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{autodesktop}\Roster Builder"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[Code]
{ The program writes its data to %APPDATA%\RosterBuilder; uninstalling never removes it. Tell the user. }
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if not UninstallSilent then
    Result := MsgBox(FmtMessage(CustomMessage('KeepDataQuestion'), [ExpandConstant('{userappdata}\RosterBuilder')]),
                     mbConfirmation, MB_YESNO) = IDYES;
end;
