# HP2P — Segurança

## Modelo

* **ID** (ex.: `589-271-473`) é público: só localiza o computador. Não autentica nada.
* **Senha** é obrigatória e é provada sem ser transmitida (SRP-6a).
* **Autorização local** é obrigatória: o usuário do computador decide e escolhe as permissões.
* **Servidor de sinalização** é considerado não confiável: não recebe senhas e um MITM nele é detectado.

## Senhas e armazenamento

| Tipo | Como funciona |
|---|---|
| Automática | 12 caracteres (`secrets`, maiúsculas/minúsculas/números, sem ambíguos). Nova a cada início ou em "Gerar nova senha". Existe em texto só na memória para exibir/copiar. |
| Personalizada | Mínimo 10 caracteres e 3 classes. Persistida apenas como verificador; não pode ser exibida. |
| Temporária | Adicional, com validade (5 min a 24 h). Expira sozinha ou pode ser removida. |

Verificador guardado (nunca a senha):

```
P' = Argon2id(senha, salt_kdf 16 B, m = 64 MiB, t = 2, p = 1, 32 B)
x  = SHA-256(salt_srp | SHA-256("hp2p:" | hex(P')))          (RFC 5054)
v  = g^x mod N                                               (grupo RFC 5054 de 2048 bits)
```

O arquivo `%APPDATA%\HP2P\credentials.bin` (ID + verificadores) é cifrado com **DPAPI** (chave do usuário
do Windows). Quem obtiver o verificador ainda precisa pagar um Argon2id de 64 MiB por senha testada.

## Protocolo de autenticação

Escolha: **SRP-6a (RFC 2945/5054)**, um PAKE aumentado padronizado. O agente usa a biblioteca `srp` (pysrp)
no modo RFC 5054; o navegador implementa o cliente espelhando exatamente essas fórmulas, com hash e Argon2id da
biblioteca hash-wasm. A interoperabilidade é verificada por `tests/test_auth.py`, que executa o JavaScript real
do navegador contra o Python real do agente.

| Ameaça | Mitigação | Teste |
|---|---|---|
| Exposição da senha | Senha nunca trafega; só A, B, M, HAMK | — |
| Replay | `sid` e B novos por tentativa; desafio descartado após uso | ✅ test_auth 4, 5 |
| Interceptação / MITM na sinalização | HMAC(K, fingerprints DTLS vistas por cada lado) nos dois sentidos; mídia só é liberada após conferir a fingerprint da MediaConnection pelo canal já autenticado | ✅ test_auth 6 |
| Personificação do agente | HAMK + bind provam que o agente conhece o verificador; o navegador aborta se falhar | ✅ test_auth 2 |
| Tentativas ilimitadas | 5 falhas em 5 min → bloqueio de 5 min (dobra a cada novo bloqueio, até 1 h); máx. 20 hellos/min; 1 sessão por vez | ✅ test_auth 7 |
| Troca de senha | Invalida a anterior na hora e cancela autenticações em curso | ✅ test_auth 8, 9 |
| Spam de janelas no PC remoto | A senha é exigida **antes** da janela de autorização | ✅ e2e |

> **Ordem senha → autorização local.** O roteiro original sugeria autorização antes do desafio. Inverti a ordem
> de propósito: como o ID é público, pedir autorização antes da senha deixaria qualquer pessoa fazer o computador
> abrir janelas de solicitação, o que favorece engenharia social. Nada é liberado antes dos dois passos.

> **Revisão especializada.** Nenhum protocolo novo foi inventado: SRP-6a, Argon2id, HMAC-SHA256 e o vínculo a
> fingerprints DTLS são construções padronizadas. Mesmo assim, o cliente SRP em JavaScript foi escrito para este
> projeto e deve passar por revisão criptográfica antes do uso comercial. Ele usa BigInt sem garantia de tempo
> constante; o expoente é efêmero e de uso único, o que limita, mas não elimina, esse risco. Uma alternativa a
> avaliar é o OPAQUE (RFC 9807), com bibliotecas auditadas (opaque-ke), que exigiria um binding Python.

## Sinalização pelo PeerJS Cloud (padrão)

O servidor público `0.peerjs.com` (WSS) é de **terceiros**. O que ele **vê**:
* IDs dos peers (`hp2p-<ID>` e `op-…`);
* SDP/ICE, que incluem endereços IP públicos e privados e as fingerprints DTLS.

O que ele **não vê**: senhas, verificadores, tela e comandos. A autenticação é SRP fim-a-fim, e a mídia e os
dados vão por DTLS/SRTP direto entre os peers. Se o servidor tentar se colocar no meio (trocar SDPs),
**o vínculo às fingerprints DTLS detecta e a sessão é recusada** (teste 6 de `tests/test_auth.py`).

Riscos aceitos com essa escolha:
* **Disponibilidade:** o serviço é gratuito, sem garantia de funcionamento.
* **Exposição de metadados:** IDs e IPs passam por terceiros.
* **ID compartilhado:** o espaço de nomes é de todos os usuários do PeerJS. Se o ID já estiver em uso,
  o agente gera outro automaticamente.

Os servidores TURN públicos do PeerJS (`eu-0`/`us-0.turn.peerjs.com`, credencial pública) só retransmitem
pacotes já cifrados.

Para produção comercial, recomenda-se o PeerServer próprio (`server/peer-server`), já implementado com
chave própria, limites de abuso e TURN com credenciais temporárias.

## Comunicação

* Sinalização em **WSS** (PeerJS Cloud ou `server/peer-server` com certificado em `config.json`).
* WebRTC: **DTLS** (DataChannel) e **DTLS-SRTP** (vídeo), obrigatórios.
* PeerServer com **chave própria**, **descoberta de IDs desligada**, limite de conexões WS por IP, de
  clientes simultâneos por IP e de mensagens por cliente, lista de origens permitidas e cabeçalhos de segurança
  (CSP, `nosniff`, `frame-ancestors 'none'`).
* **TURN** com credenciais temporárias (`use-auth-secret`) e bloqueio de relay para redes internas.
* Bibliotecas do controlador servidas localmente com **SRI** (sem CDN de terceiros).

## Controle remoto

* Somente eventos de mouse/teclado de uma **lista fixa** (SendInput). Não existe nenhum caminho para executar
  comandos, scripts ou arquivos.
* Coordenadas validadas (0..1, finitas), códigos de tecla em lista branca, mensagens ≤ 16 KB, limite de
  400 eventos/s.
* Permissões por sessão: **somente visualizar**, **mouse** ou **mouse e teclado**.
* Todas as teclas e botões são liberados ao encerrar ou perder a conexão.
* Indicador vermelho **sempre visível** durante a sessão, com botão Encerrar; ícone da bandeja muda de cor.

## Limitações conhecidas

* **Ctrl+Alt+Del** não pode ser injetado (SAS do Windows). Janelas de **administrador** e a **tela do UAC**
  não recebem entrada de um processo comum (UIPI). O operador recebe um aviso. Rodar o agente como serviço
  SYSTEM resolveria, mas fica para uma versão futura.
* Logs ficam em `%APPDATA%\HP2P\logs` e **não contêm senhas, verificadores nem chaves**. Contêm IDs, IPs de
  conexão e eventos de sessão (auditoria).
