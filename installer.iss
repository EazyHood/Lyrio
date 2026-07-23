; Lyrio - script de Inno Setup (compilar con iscc installer.iss)
; Requiere haber generado dist\Lyrio.exe con build.bat

[Setup]
AppName=Lyrio
AppVersion=1.1.0
AppPublisher=EazyHood
AppPublisherURL=https://github.com/EazyHood/Lyrio
DefaultDirName={autopf}\Lyrio
DefaultGroupName=Lyrio
UninstallDisplayIcon={app}\Lyrio.exe
OutputBaseFilename=Lyrio-Setup
OutputDir=dist
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "dist\Lyrio.exe"; DestDir: "{app}"
Source: "LICENSE"; DestDir: "{app}"

[Icons]
Name: "{group}\Lyrio"; Filename: "{app}\Lyrio.exe"
Name: "{autodesktop}\Lyrio"; Filename: "{app}\Lyrio.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear icono en el escritorio"; Flags: unchecked

[Run]
Filename: "{app}\Lyrio.exe"; Description: "Abrir Lyrio"; Flags: postinstall nowait skipifsilent
