# HP2P — Acesso Remoto

Acesso e controle remoto de computadores Windows, com conexão direta (P2P) e criptografada entre os
computadores.

## Como usar

**1. No computador que será acessado:** baixe e instale o HP2P.

➡️ **[Baixar o instalador (Windows 10/11)](https://github.com/hebertwng/hp2p/releases/latest/download/HP2P-Instalador-1.0.0.exe)**

Na instalação, escolha **"Este computador será ACESSADO"**. O HP2P abre mostrando o **ID** e a **senha**.

**2. No computador que vai acessar:** não precisa instalar nada. Abra a página de acesso:

➡️ **[Página de acesso — https://hebertwng.github.io/hp2p/](https://hebertwng.github.io/hp2p/)**

Informe o **ID** e a **senha** e clique em **Conectar**.

**3. No computador acessado:** clique em **Aceitar conexão** e escolha as permissões:
visualizar, controlar o mouse ou controlar mouse e teclado.

Uma faixa vermelha fica visível durante toda a sessão, com o botão **Encerrar**.

## Como funciona

* **Navegador ⇄ HP2P:** a página de acesso (PeerJS) e o HP2P (Python/aiortc) se conectam por WebRTC. A tela
  vai como vídeo e o mouse e o teclado por um canal de dados, direto entre os computadores.
* **Localização:** os computadores se encontram pelo serviço público PeerJS Cloud. Depois da conexão, nada
  passa por ele.
* **Senha:** nunca é transmitida. O navegador prova que a conhece (SRP-6a), e o computador guarda apenas um
  verificador protegido pelo Windows.
* **Controle:** nada é liberado sem a senha correta **e** a aprovação de quem está no computador acessado.
  Falhas seguidas bloqueiam novas tentativas temporariamente.
* **Rede:** funciona na mesma rede e entre redes diferentes. Quando a conexão direta não é possível, ela é
  retransmitida por TURN, ainda criptografada.

Documentação técnica: [arquitetura](docs/architecture.md) · [segurança](docs/security.md) ·
[instalação e build](docs/installation.md)
