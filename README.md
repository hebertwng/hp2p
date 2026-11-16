# HP2P — Acesso Remoto

Acesso e controle remoto de computadores Windows, com conexão direta (P2P) e criptografada entre os
computadores. Nada para instalar: um executável de um lado e uma página web do outro.

## Como usar

**1. No computador que será acessado:** baixe e execute o **HP2P.exe**. Não precisa instalar.

➡️ **[Baixar HP2P.exe (Windows 10/11)](https://github.com/hebertwng/hp2p/releases/latest/download/HP2P.exe)**

O HP2P abre mostrando o **ID** e a **senha** deste computador.

**2. No computador que vai acessar:** abra a página de acesso no navegador:

➡️ **[Página de acesso — https://hebertwng.github.io/hp2p/](https://hebertwng.github.io/hp2p/)**

Informe o **ID** e a **senha** e clique em **Conectar**.

**3. No computador acessado:** clique em **Aceitar conexão** e escolha as permissões:
visualizar, controlar o mouse ou controlar mouse e teclado.

Uma faixa vermelha fica visível durante toda a sessão, com o botão **Encerrar**.

> O executável não é assinado digitalmente. Se o Windows SmartScreen avisar, clique em **"Mais informações"**
> e depois em **"Executar assim mesmo"**.

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

Documentação técnica: [arquitetura](docs/architecture.md)
