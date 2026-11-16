"""Adaptador de sinalização: implementa o lado CLIENTE do protocolo PeerJS/PeerServer em Python.

O aiortc não fala o protocolo do PeerServer. Este módulo reproduz exatamente o que a
biblioteca PeerJS (navegador) faz no socket de sinalização:

  * conecta em  ws(s)://HOST:PORT{path}peerjs?key=KEY&id=ID&token=TOKEN&version=...
  * aguarda {"type": "OPEN"}  (ou ID-TAKEN / INVALID-KEY / ERROR)
  * envia {"type": "HEARTBEAT"} a cada 5 s (senão o PeerServer derruba o cliente)
  * envia mensagens no formato {"type", "payload", "dst"}; o servidor acrescenta "src"
  * reconecta automaticamente reutilizando o MESMO id e token (o PeerServer aceita)

Não há nenhuma troca de dados de aplicação por aqui: só SDP/ICE (sinalização).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import aiohttp

log = logging.getLogger("hp2p.signal")

PEERJS_PROTOCOL_VERSION = "1.5.4"  # versão do PeerJS usada no controlador web
HEARTBEAT_INTERVAL = 5.0

MessageHandler = Callable[[dict], Awaitable[None] | None]


class SignalingError(Exception):
    def __init__(self, msg: str, code: str = "error") -> None:
        super().__init__(msg)
        self.code = code  # "ID-TAKEN" | "INVALID-KEY" | "error"


class PeerJSSignaling:
    def __init__(
        self,
        peer_id: str,
        token: str,
        host: str = "localhost",
        port: int = 9000,
        path: str = "/signal",
        key: str = "peerjs",
        secure: bool = False,
        verbose: bool = False,
    ) -> None:
        self.peer_id = peer_id
        self.token = token
        self.host = host
        self.port = port
        self.path = path if path.endswith("/") else path + "/"
        if not self.path.startswith("/"):
            self.path = "/" + self.path
        self.key = key
        self.secure = secure
        self.verbose = verbose

        self.on_message: MessageHandler | None = None
        # on_state(estado, detalhe): "connecting" | "connected" | "reconnecting" | "error"
        self.on_state: Callable[[str, str], None] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._open = asyncio.Event()
        self._closing = False
        self._runner: asyncio.Task | None = None

    # ------------------------------------------------------------------ URL
    @property
    def url(self) -> str:
        scheme = "wss" if self.secure else "ws"
        return (
            f"{scheme}://{self.host}:{self.port}{self.path}peerjs"
            f"?key={self.key}&id={self.peer_id}&token={self.token}"
            f"&version={PEERJS_PROTOCOL_VERSION}"
        )

    @property
    def is_open(self) -> bool:
        return self._open.is_set()

    # ------------------------------------------------------------ ciclo de vida
    def _state(self, state: str, detail: str = "") -> None:
        if self.on_state:
            try:
                self.on_state(state, detail)
            except Exception:  # noqa: BLE001
                log.exception("on_state falhou")

    async def start(self) -> None:
        """Conecta e registra o ID. Lança SignalingError se o registro falhar."""
        self._session = aiohttp.ClientSession()
        self._state("connecting", f"{self.host}:{self.port}")
        await self._connect_once()
        self._runner = asyncio.create_task(self._run_forever(), name="signaling")

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):
                pass
        if self._session:
            await self._session.close()

    async def _connect_once(self) -> None:
        assert self._session is not None
        log.info("Conectando ao PeerServer: %s", self.url.split("&token=")[0] + "&token=***")
        self._ws = await self._session.ws_connect(self.url, heartbeat=None, autoping=True)

        # A primeira mensagem do PeerServer confirma (OPEN) ou rejeita o registro.
        first = await self._ws.receive(timeout=10)
        if first.type != aiohttp.WSMsgType.TEXT:
            raise SignalingError(f"Resposta inesperada do PeerServer: {first.type}")
        msg = json.loads(first.data)
        mtype = msg.get("type")
        if mtype == "OPEN":
            self._open.set()
            log.info("ID registrado no PeerServer: %s", self.peer_id)
            self._state("connected", f"{self.host}:{self.port}")
            return
        detail = (msg.get("payload") or {}).get("msg", "")
        await self._ws.close()
        if mtype == "ID-TAKEN":
            raise SignalingError(f"ID já está em uso por outro cliente: {self.peer_id}", "ID-TAKEN")
        if mtype == "INVALID-KEY":
            raise SignalingError("Chave (key) inválida para o servidor de sinalização", "INVALID-KEY")
        raise SignalingError(f"PeerServer recusou o registro: {mtype} {detail}")

    async def _run_forever(self) -> None:
        backoff = 1.0
        while not self._closing:
            hb = asyncio.create_task(self._heartbeat())
            try:
                await self._read_loop()
            except Exception as exc:  # noqa: BLE001
                log.warning("Erro na sinalização: %r", exc)
            finally:
                hb.cancel()
                self._open.clear()
            if self._closing:
                break
            log.warning("Conexão com o PeerServer perdida. Reconectando com o mesmo ID...")
            self._state("reconnecting", "conexão perdida")
            while not self._closing:
                await asyncio.sleep(backoff)
                try:
                    await self._connect_once()
                    log.info("Reconectado ao PeerServer (ID %s mantido)", self.peer_id)
                    backoff = 1.0
                    break
                except Exception as exc:  # noqa: BLE001
                    backoff = min(backoff * 2, 30.0)
                    log.warning("Falha ao reconectar (%s). Nova tentativa em %.0fs", exc, backoff)
                    self._state("reconnecting", str(exc))

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            if raw.type == aiohttp.WSMsgType.TEXT:
                try:
                    msg = json.loads(raw.data)
                except json.JSONDecodeError:
                    log.warning("Mensagem de sinalização inválida: %r", raw.data[:200])
                    continue
                if self.verbose:
                    log.debug("<< %s", _short(msg))
                if self.on_message:
                    try:
                        res = self.on_message(msg)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:  # noqa: BLE001
                        log.exception("Erro tratando mensagem de sinalização %s", msg.get("type"))
            elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._send_raw({"type": "HEARTBEAT"})

    # ------------------------------------------------------------------ envio
    async def send(self, mtype: str, payload: dict, dst: str) -> None:
        msg = {"type": mtype, "payload": payload, "dst": dst}
        if self.verbose:
            log.debug(">> %s", _short(msg))
        await self._send_raw(msg)

    async def _send_raw(self, msg: dict) -> None:
        if self._ws is None or self._ws.closed:
            # aguarda uma eventual reconexão por até 10 s
            try:
                await asyncio.wait_for(self._open.wait(), 10)
            except asyncio.TimeoutError:
                raise SignalingError("Sem conexão com o PeerServer") from None
        assert self._ws is not None
        await self._ws.send_str(json.dumps(msg))


def _short(msg: dict) -> str:
    s = json.dumps(msg, ensure_ascii=False)
    return s if len(s) < 300 else s[:300] + "..."
