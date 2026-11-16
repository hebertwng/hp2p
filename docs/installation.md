# HP2P — Instalação, execução e testes

O HP2P usa o **PeerJS Cloud** público (`0.peerjs.com`) como servidor de sinalização: **não é preciso
instalar nem manter servidor**. O servidor só apresenta os dois computadores; a tela e os comandos
trafegam direto entre eles (P2P, criptografado). Um PeerServer próprio continua disponível como opção
(seção 6).

## 1. Instalador (para os dois computadores)

Arquivo: `dist\HP2P-Instalador-1.0.0.exe` (~63 MB). Não pede administrador (instala só para o usuário atual).
Ele pergunta **"Como este computador será usado?"**:

| Escolha | O que acontece |
|---|---|
| **Este computador será ACESSADO** | Instala o HP2P em `%LOCALAPPDATA%\Programs\HP2P`, cria o atalho no Menu Iniciar e, se marcado, na Área de Trabalho e no início com o Windows. Ao final, abre o HP2P, que mostra o **ID** e a **senha**. |
| **Este computador vai ACESSAR outro** | Copia a página `HP2P-Acesso.html` (arquivo único) e cria o atalho "HP2P Acesso Remoto". Ao final, abre a página no navegador padrão. O agente **não** é instalado. |

Desinstalação: Configurações do Windows → Aplicativos → **HP2P Acesso Remoto**.

Implantação silenciosa (GPO/script):

```bash
HP2P-Instalador-1.0.0.exe /VERYSILENT /ROLE=agent /TASKS="startup"
HP2P-Instalador-1.0.0.exe /VERYSILENT /ROLE=operator /TASKS="operatoricon"
```

Na primeira execução do agente:
* o SmartScreen pode avisar, porque o executável não é assinado: clique em "Mais informações" e depois em
  "Executar assim mesmo";
* o firewall pode perguntar: permita.

## 2. Teste entre dois computadores

1. **Computador A (será acessado):** execute o instalador e escolha **"será ACESSADO"**. Anote o ID e a senha.
2. **Computador B (vai acessar):** execute o instalador e escolha **"vai ACESSAR"**. Na página que abrir,
   informe o ID, a senha e seu nome, e clique em **Conectar**.
3. **No computador A:** clique em **Aceitar conexão** e escolha as permissões.
4. A tela de A aparece em B. A barra superior mostra a rota:
   * "P2P host⇄host": conexão direta na mesma rede;
   * "P2P srflx⇄srflx": conexão direta entre redes diferentes, atravessando o NAT com STUN;
   * "via TURN": conexão retransmitida pelos servidores TURN públicos do PeerJS.

Funciona na mesma rede e em redes diferentes, sem nenhuma configuração.

## 3. Operação

* **Controlar:** clique na tela remota. Use **Tela cheia** (Keyboard Lock do Chrome/Edge) para enviar Esc,
  Alt+Tab e a tecla Windows, ou use o menu **Atalhos**.
* **No computador acessado:** há uma faixa vermelha "Sessão remota ativa — Encerrar" sempre visível. O botão
  **Encerrar conexão** e o menu da bandeja também encerram a sessão.
* **Dados do agente:** `%APPDATA%\HP2P\`, com `settings.json`, `credentials.bin` (DPAPI) e `logs\agent.log`.

## 4. Gerar o instalador

Pré-requisitos no computador de desenvolvimento: Python 3.12 com o `.venv` do agente e o Inno Setup 6
(`winget install JRSoftware.InnoSetup --scope user`).

```bash
agent\.venv\Scripts\python build\build_installer.py
```

Ele gera `dist\HP2P\HP2P.exe` (PyInstaller, com Python, Qt, aiortc/FFmpeg e demais bibliotecas embutidos),
`dist\HP2P-Acesso.html` e `dist\HP2P-Instalador-1.0.0.exe`. Recomenda-se assinar os executáveis com um
certificado de *code signing*.

Rodar o agente pelo código-fonte:

```bash
cd agent
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python main.py
```

## 5. Testes automatizados

```bash
agent\.venv\Scripts\python tests\test_auth.py                # SRP JS<->Python, replay, MITM, bloqueio, senhas
agent\.venv\Scripts\python tests\test_input_units.py         # mapeamento de mouse/teclado (sem injetar eventos)
agent\.venv\Scripts\python tests\test_service_reconnect.py   # reconexão à sinalização sem laços paralelos
agent\.venv\Scripts\python tests\e2e_agent.py                # agente real sem interface (decisão automática)
agent\.venv\Scripts\python tests\gui_snapshot.py docs\img    # capturas das telas do agente
```

## 6. Opcional: PeerServer próprio

Para não depender do PeerJS Cloud (disponibilidade, privacidade dos metadados, requisitos corporativos):

1. Num servidor acessível pelos dois lados, com Node.js 18+, rode `npm install` e depois `npm start` em
   `server/peer-server` (padrão: porta 9443, `/signal`, chave `hp2p`).
   * Configure HTTPS em `config.json`. Para testes, use `gerar-certificado.ps1`.
   * Para TURN próprio, use `server/turn/turnserver.conf`, com credenciais temporárias em `/hp2p/ice`.
2. **Agente:** instale com `/SERVER=host /PORT=9443 /SECURE=1` ou altere em Configurações → Servidor.
3. **Controlador:** em `web-controller/config.js`, troque `signaling` para o seu servidor
   (`path: "/signal"`, `key: "hp2p"`) e `iceEndpoint: "/hp2p/ice"`. Depois gere a página de novo, ou acesse
   `https://servidor:9443/`, que já entrega o controlador.
