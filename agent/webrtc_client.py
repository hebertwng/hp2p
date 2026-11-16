"""Adaptador PeerJS <-> aiortc (isolado neste módulo).

Implementa, sobre o aiortc, o que a biblioteca PeerJS espera de um peer:

  DataConnection (o NAVEGADOR inicia):  OFFER{type:"data"} -> ANSWER; CANDIDATEs do navegador
      são aplicados; o DataChannel chega via "datachannel". Serialização suportada: "json"
      (JSON UTF-8 em frames binários, como o PeerJS 1.5) e "raw".
  MediaConnection (o AGENTE inicia):    o agente cria a oferta com a trilha da tela e envia
      OFFER{type:"media"}; o navegador recebe o evento "call", responde (ANSWER) e faz trickle.

Particularidades validadas na POC (ver docs/architecture.md):
  * aiortc não faz trickle ICE: todos os candidatos locais vão no SDP;
  * conexões de mídia iniciadas pelo navegador são recusadas (o agente só ENVIA tela).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Callable

import aiohttp
from aiortc import (RTCConfiguration, RTCDataChannel, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription)
from aiortc.sdp import candidate_from_sdp

from signaling import PeerJSSignaling

log = logging.getLogger("hp2p.webrtc")

MAX_MESSAGE_BYTES = 16 * 1024


def _spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    t.add_done_callback(lambda task: (not task.cancelled() and task.exception())
                        and log.error("tarefa falhou: %r", task.exception(), exc_info=task.exception()))
    return t


def describe_route(pc: RTCPeerConnection) -> dict:
    """Par ICE nomeado (diagnóstico; lê atributos internos do aioice)."""
    try:
        conn = None
        if pc.sctp is not None:
            conn = pc.sctp.transport.transport._connection
        else:
            for t in pc.getTransceivers():
                conn = t.sender.transport.transport._connection
                break
        pair = next(iter(conn._nominated.values()))
        lc, rc = pair.local_candidate, pair.remote_candidate
        return {"local": f"{lc.type} {lc.host}:{lc.port}", "remote": f"{rc.type} {rc.host}:{rc.port}",
                "remote_ip": rc.host, "relay": "relay" in (lc.type, rc.type)}
    except Exception:  # noqa: BLE001
        return {}


class _Conn:
    """Base: uma RTCPeerConnection associada a um connectionId do PeerJS."""

    def __init__(self, adapter: "PeerJSAdapter", remote_id: str, connection_id: str, pc: RTCPeerConnection):
        self.adapter = adapter
        self.remote_id = remote_id
        self.connection_id = connection_id
        self.pc = pc
        self.remote_ready = asyncio.Event()
        self.closed = False
        self.created = time.monotonic()
        self.on_close: Callable[["_Conn", str], None] | None = None
        self.on_state: Callable[[str], None] | None = None

        @pc.on("connectionstatechange")
        async def _state() -> None:
            st = pc.connectionState
            log.info("[%s] PeerConnection %s", self.connection_id, st)
            if self.on_state:
                self.on_state(st)
            if st in ("failed", "closed"):
                await self.close(f"connectionState={st}")

    @property
    def local_fp(self) -> str:
        from authentication import sdp_fingerprint
        return sdp_fingerprint(self.pc.localDescription.sdp)

    @property
    def remote_fp(self) -> str:
        from authentication import sdp_fingerprint
        return sdp_fingerprint(self.pc.remoteDescription.sdp)

    async def add_candidate(self, ice: dict) -> None:
        cand_str = (ice or {}).get("candidate") or ""
        if not cand_str:
            return
        try:
            await asyncio.wait_for(self.remote_ready.wait(), 20)
        except asyncio.TimeoutError:
            return
        if cand_str.startswith("candidate:"):
            cand_str = cand_str[len("candidate:"):]
        try:
            cand = candidate_from_sdp(cand_str)
        except Exception:  # noqa: BLE001
            log.debug("candidato inválido ignorado")
            return
        cand.sdpMid = ice.get("sdpMid")
        cand.sdpMLineIndex = ice.get("sdpMLineIndex")
        await self.pc.addIceCandidate(cand)

    async def close(self, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self.adapter._conns.pop(self.connection_id, None)
        log.info("[%s] encerrada (%s)", self.connection_id, reason)
        try:
            await self.pc.close()
        except Exception:  # noqa: BLE001
            pass
        if self.on_close:
            try:
                self.on_close(self, reason)
            except Exception:  # noqa: BLE001
                log.exception("on_close falhou")


class DataConn(_Conn):
    def __init__(self, adapter, remote_id, connection_id, pc, serialization: str, metadata: Any):
        super().__init__(adapter, remote_id, connection_id, pc)
        self.serialization = serialization
        self.metadata = metadata
        self.channel: RTCDataChannel | None = None
        self.opened = asyncio.Event()
        self.on_message: Callable[[Any], None] | None = None

        @pc.on("datachannel")
        def _dc(channel: RTCDataChannel) -> None:
            self.channel = channel

            @channel.on("message")
            def _m(data: Any) -> None:
                if isinstance(data, (bytes, bytearray, str)) and len(data) > MAX_MESSAGE_BYTES:
                    log.warning("[%s] mensagem grande demais descartada", self.connection_id)
                    return
                try:
                    obj = self._decode(data)
                except Exception:  # noqa: BLE001
                    log.warning("[%s] mensagem inválida descartada", self.connection_id)
                    return
                if self.on_message:
                    self.on_message(obj)

            @channel.on("close")
            def _c() -> None:
                _spawn(self.close("DataChannel fechado"))

            log.info("[%s] DataChannel aberto (label=%s)", self.connection_id, channel.label)
            self.opened.set()

    def _decode(self, data: Any) -> Any:
        if self.serialization == "json":
            text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data
            return json.loads(text)
        return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data

    def send(self, obj: Any) -> bool:
        ch = self.channel
        if ch is None or ch.readyState != "open":
            return False
        if self.serialization == "json":
            ch.send(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        else:
            ch.send(obj if isinstance(obj, (str, bytes)) else json.dumps(obj))
        return True


class MediaCall(_Conn):
    pass


class PeerJSAdapter:
    def __init__(self, signaling: PeerJSSignaling, ice_provider: Callable[[], "asyncio.Future"]):
        self.signaling = signaling
        self.ice_provider = ice_provider
        self._conns: dict[str, _Conn] = {}
        self.on_data_connection: Callable[[DataConn], None] | None = None
        self.accepting = True
        signaling.on_message = self._on_signal

    async def _new_pc(self) -> RTCPeerConnection:
        servers = await self.ice_provider()
        return RTCPeerConnection(RTCConfiguration(iceServers=servers))

    # ------------------------------------------------------------ sinalização
    async def _on_signal(self, msg: dict) -> None:
        mtype, src, payload = msg.get("type"), msg.get("src"), msg.get("payload") or {}
        if not isinstance(payload, dict):
            return
        if mtype == "OFFER":
            _spawn(self._on_offer(src, payload))
        elif mtype == "ANSWER":
            _spawn(self._on_answer(src, payload))
        elif mtype == "CANDIDATE":
            c = self._conns.get(str(payload.get("connectionId", "")))
            if c and c.remote_id == src:
                _spawn(c.add_candidate(payload.get("candidate") or {}))
        elif mtype in ("LEAVE", "EXPIRE"):
            for c in [c for c in self._conns.values() if c.remote_id == src]:
                _spawn(c.close(f"{mtype} do peer"))

    async def _on_offer(self, src: str, payload: dict) -> None:
        conn_id = str(payload.get("connectionId", ""))[:64]
        if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", conn_id) or conn_id in self._conns:
            return
        if payload.get("type") != "data":
            log.warning("Oferta de mídia vinda de %s recusada (o agente só envia tela)", src)
            return
        serialization = payload.get("serialization") or "binary"
        if serialization not in ("json", "raw"):
            log.warning("Serialização %s não suportada (use json); conexão recusada", serialization)
            return
        if not self.accepting:
            log.info("Serviço pausado: conexão de %s ignorada", src)
            return
        sdp = payload.get("sdp") or {}
        if sdp.get("type") != "offer" or not isinstance(sdp.get("sdp"), str) or len(sdp["sdp"]) > 20000:
            return
        pc = await self._new_pc()
        conn = DataConn(self, src, conn_id, pc, serialization, payload.get("metadata"))
        self._conns[conn_id] = conn
        log.info("Solicitação de conexão de %s (connectionId=%s)", src, conn_id)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp["sdp"], type="offer"))
        conn.remote_ready.set()
        await pc.setLocalDescription(await pc.createAnswer())
        await self.signaling.send("ANSWER", {"sdp": {"type": "answer", "sdp": pc.localDescription.sdp},
                                             "type": "data", "connectionId": conn_id,
                                             "browser": "hp2p-agent"}, src)
        if self.on_data_connection:
            self.on_data_connection(conn)

    async def _on_answer(self, src: str, payload: dict) -> None:
        c = self._conns.get(str(payload.get("connectionId", "")))
        if not isinstance(c, MediaCall) or c.remote_id != src or c.remote_ready.is_set():
            return
        sdp = payload.get("sdp") or {}
        if sdp.get("type") != "answer" or not isinstance(sdp.get("sdp"), str):
            return
        await c.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp["sdp"], type="answer"))
        c.remote_ready.set()
        log.info("[%s] ANSWER de mídia recebido", c.connection_id)

    # ------------------------------------------------------------ mídia (saída)
    async def prepare_call(self, remote_id: str, track) -> MediaCall:
        """Cria a PeerConnection de mídia e a oferta (coleta de ICE ~ alguns segundos) SEM enviá-la.

        Permite adiantar a coleta enquanto o usuário local decide na janela de autorização.
        """
        conn_id = "mc_" + secrets.token_hex(8)
        pc = await self._new_pc()
        call = MediaCall(self, remote_id, conn_id, pc)
        self._conns[conn_id] = call
        pc.addTransceiver(track, direction="sendonly")
        await pc.setLocalDescription(await pc.createOffer())
        return call

    async def send_call(self, call: MediaCall, metadata: dict) -> None:
        pc = call.pc
        await self.signaling.send("OFFER", {"sdp": {"type": "offer", "sdp": pc.localDescription.sdp},
                                            "type": "media", "connectionId": call.connection_id,
                                            "metadata": metadata, "browser": "hp2p-agent"}, call.remote_id)
        log.info("[%s] chamada de mídia enviada para %s", call.connection_id, call.remote_id)

    async def call(self, remote_id: str, track, metadata: dict) -> MediaCall:
        c = await self.prepare_call(remote_id, track)
        await self.send_call(c, metadata)
        return c

    async def close_all(self) -> None:
        for c in list(self._conns.values()):
            await c.close("encerramento do agente")


async def fetch_ice_servers(url: str | None, fallback: list[dict]) -> list[RTCIceServer]:
    """Obtém iceServers (com credenciais TURN temporárias) do servidor; usa o fallback se falhar."""
    servers = fallback
    if url:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
                async with s.get(url) as r:
                    if r.status == 200:
                        servers = (await r.json()).get("iceServers") or fallback
        except Exception as exc:  # noqa: BLE001
            log.debug("iceServers do servidor indisponíveis (%s); usando configuração local", exc)
    out = []
    for s in servers:
        urls = s.get("urls")
        if urls:
            out.append(RTCIceServer(urls=urls, username=s.get("username"), credential=s.get("credential")))
    return out
