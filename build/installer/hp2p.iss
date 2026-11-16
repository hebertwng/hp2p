; HP2P - instalador (Inno Setup 6)
;
; Pergunta o papel do computador:
;   * "Será ACESSADO" -> instala o agente HP2P.exe (por usuário, sem administrador) e o abre ao final.
;   * "Vai ACESSAR"   -> copia a página HP2P-Acesso.html (controlador web, arquivo único) e a abre
;                        no navegador ao final. O agente NÃO é instalado.
;
; Sinalização: PeerJS Cloud público (0.peerjs.com) — não é preciso servidor próprio.
; Implantação silenciosa:
;   HP2P-Instalador.exe /VERYSILENT /ROLE=agent|operator [/TASKS="startup,desktopicon"]
;   PeerServer próprio (opcional, só agente):  /SERVER=host /PORT=9443 /SECURE=1

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AgentDir "..\..\dist\HP2P"
#define AccessHtml "..\..\dist\HP2P-Acesso.html"

[Setup]
AppId={{7C3E2A61-4B8D-4F0E-9C2A-5D1B6E8F9A30}
AppName=HP2P Acesso Remoto
AppVersion={#AppVersion}
AppPublisher=HP2P
DefaultDirName={autopf}\HP2P
DefaultGroupName=HP2P
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=HP2P-Instalador-{#AppVersion}
SetupIconFile=..\pyinstaller\hp2p.ico
UninstallDisplayIcon={app}\unins000.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho do HP2P na Área de Trabalho"; Check: IsAgent
Name: "startup"; Description: "Iniciar o HP2P com o Windows (minimizado na bandeja)"; Check: IsAgent
Name: "operatoricon"; Description: "Criar atalho ""HP2P Acesso Remoto"" na Área de Trabalho"; Check: IsOperator

[Files]
Source: "{#AgentDir}\*"; DestDir: "{app}"; Excludes: "settings.json"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsAgent
Source: "{#AccessHtml}"; DestDir: "{app}"; Flags: ignoreversion; Check: IsOperator

[Icons]
; computador ACESSADO
Name: "{autoprograms}\HP2P"; Filename: "{app}\HP2P.exe"; Check: IsAgent
Name: "{autodesktop}\HP2P"; Filename: "{app}\HP2P.exe"; Tasks: desktopicon
; computador que ACESSA
Name: "{autoprograms}\HP2P Acesso Remoto"; Filename: "{app}\HP2P-Acesso.html"; Check: IsOperator
Name: "{autodesktop}\HP2P Acesso Remoto"; Filename: "{app}\HP2P-Acesso.html"; Tasks: operatoricon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "HP2P"; \
  ValueData: """{app}\HP2P.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\HP2P.exe"; Description: "Abrir o HP2P agora"; Flags: nowait postinstall skipifsilent; Check: IsAgent
Filename: "{app}\HP2P-Acesso.html"; Description: "Abrir a página de acesso remoto no navegador"; Flags: shellexec nowait postinstall skipifsilent; Check: IsOperator

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM HP2P.exe /F"; Flags: runhidden; RunOnceId: "StopHP2P"

[Code]
var
  RolePage: TInputOptionWizardPage;

function IsAgent: Boolean;
begin
  Result := (RolePage <> nil) and RolePage.Values[0];
end;

function IsOperator: Boolean;
begin
  Result := (RolePage <> nil) and RolePage.Values[1];
end;

procedure InitializeWizard;
begin
  RolePage := CreateInputOptionPage(wpWelcome,
    'Como este computador será usado?',
    'Escolha o papel deste computador no acesso remoto.',
    'O HP2P tem duas partes. Selecione a que corresponde a este computador:',
    True, False);
  RolePage.Add('Este computador será ACESSADO remotamente' + #13#10 +
               '   (instala o HP2P, que mostra o ID e a senha deste computador)');
  RolePage.Add('Este computador vai ACESSAR outro computador' + #13#10 +
               '   (instala e abre a página de acesso no navegador)');
  if LowerCase(ExpandConstant('{param:ROLE|agent}')) = 'operator' then
    RolePage.Values[1] := True
  else
    RolePage.Values[0] := True;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  if IsAgent then
    Result := 'Papel: computador que será ACESSADO (HP2P)'
  else
    Result := 'Papel: computador que vai ACESSAR outro (página de acesso no navegador)';
  Result := Result + NewLine + NewLine + MemoDirInfo + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + MemoTasksInfo;
end;

function JsonBool(B: Boolean): String;
begin
  if B then Result := 'true' else Result := 'false';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Json, Signaling, Host: String;
begin
  if (CurStep = ssPostInstall) and IsAgent then
  begin
    Host := ExpandConstant('{param:SERVER|}');
    if Host = '' then
      Signaling := '{"host": "0.peerjs.com", "port": 443, "path": "/", "key": "peerjs", "secure": true}'
    else
      Signaling := '{"host": "' + Host + '", "port": ' + ExpandConstant('{param:PORT|9443}') +
        ', "path": "/signal", "key": "hp2p", "secure": ' + JsonBool(ExpandConstant('{param:SECURE|0}') = '1') + '}';
    { "factory_id" muda a cada instalação: o agente aplica estes valores mesmo sobre uma configuração antiga. }
    Json := '{' + #13#10 +
      '  "factory_id": "' + GetDateTimeString('yyyymmddhhnnss', #0, #0) + '",' + #13#10 +
      '  "signaling": ' + Signaling + ',' + #13#10 +
      '  "start_with_windows": ' + JsonBool(WizardIsTaskSelected('startup')) + #13#10 +
      '}';
    SaveStringToFile(ExpandConstant('{app}\settings.json'), Json, False);
  end;
end;
