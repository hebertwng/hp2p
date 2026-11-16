"""Serviço do agente e sessões remotas.

Fluxo de uma sessão (nada é liberado antes do passo 5):
  1. navegador abre uma DataConnection PeerJS (DTLS) com o agente;
  2. hello{A} -> challenge -> proof: SRP-6a + vínculo às fingerprints DTLS (authentication.py);
     falhas contam no limitador (bloqueio temporário);
  3. senha OK -> o usuário local decide na janela de autorização e escolhe as permissões;
  4. o agente chama o navegador (MediaConnection PeerJS) com a tela; a fingerprint DTLS da mídia
     é confirmada pelo DataChannel já autenticado ANTES de liberar o primeiro quadro;
  5. sessão ativa: vídeo + comandos de mouse/teclado conforme as permissões, com limite de taxa.
A sessão termina por: botão Encerrar (local), bye do operador, queda/silêncio > 20 s, troca de senha.
Em qualquer término todas as teclas e botões do mouse são liberados.

Senha antes da janela de autorização: evita que qualquer pessoa que saiba o ID (público) faça o
computador exibir janelas de solicitação (spam/engenharia social). Só quem prova a senha chega ao
usuário local.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from authentication import AuthError, AuthHandshake
from config import PROTOCOL_VERSION, Settings
from identity import display, peer_id
from keyboard_control import SHORTCUTS, KeyboardController
from mouse_control import MouseController
from password_manager import PasswordManager
from screen_capture import ScreenCapturer
from security import AttemptLimiter, TokenBucket, safe_text
from signaling import PeerJSSignaling, SignalingError
from video_encoder import ScreenVideoTrack, configure_bitrate
from webrtc_client import DataConn, MediaCall, PeerJSAdapter, describe_route, fetch_ice_servers

log = logging.getLogger("hp2p.session")

HELLO_TIMEOUT_S = 20
LOCAL_DECISION_DEFAULT_S = 60
IDLE_TIMEOUT_S = 20
MEDIA_BIND_TIMEOUT_S = 25
PERMISSIONS = ("view", "mouse", "full")


class UiCallbacks(Protocol):
    def status(self, state: str, text: str) -> None: ...
    def session(self, info: dict | None) -> None: ...
    def event(self, text: str) -> None: ...
    async def authorize(self, request: dict) -> tuple[bool, str]: ...


@dataclass
class Session:
    conn: DataConn
    state: str = "await-hello"   # await-hello | await-proof | await-local | starting | active | closed
    handshake: AuthHandshake | None = None
    operator: str = "-"
    permission: str = "view"
    credential: str = "-"
    started_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.monotonic)
    call: MediaCall | None = None
    prepared: asyncio.Future | None = None
    track: ScreenVideoTrack | None = None
    media_fp_ctrl: asyncio.Future | None = None
    mouse: MouseController | None = None
    keyboard: KeyboardController | None = None
    bucket: TokenBucket | None = None
    uipi_warned: float = 0.0
    sid: str = field(default_factory=lambda: secrets.token_hex(8))
    tasks: list = field(default_factory=list)


class AgentService:
    def __init__(self, settings: Settings, pm: PasswordManager, ui: UiCallbacks):
        self.settings = settings
        self.pm = pm
        self.ui = ui
        sec = settings.get("security")
        self.limiter = AttemptLimiter(sec["max_failures"], sec["failure_window_s"], sec["lockout_s"])
        self.signaling: PeerJSSignaling | None = None
        self.adapter: PeerJSAdapter | None = None
        self.session: Session | None = None
        self.paused = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._capturer: ScreenCapturer | None = None
        self._hello_times: list[float] = []
        self._connecting = False
        configure_bitrate(settings.get("video", "max_bitrate"))
        pm.listeners.append(self._on_password_changed)

    # ============================================================ ciclo de vida
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.ui.status("starting", "Aplicativo iniciado")
        await self._connect_signaling()
        await self._stop.wait()
        await self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    async def _connect_signaling(self) -> None:
        # Um único laço de conexão por vez (cliques repetidos em "Iniciar serviço" não criam laços paralelos)
        if self._connecting:
            return
        self._connecting = True
        try:
            await self._connect_loop()
        finally:
            self._connecting = False

    async def _connect_loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set() and not self.paused:
            sig_cfg = self.settings.get("signaling")  # relido a cada tentativa (Configurações podem mudar)
            token = secrets.token_hex(12)
            sig = PeerJSSignaling(peer_id(self.pm.device_id), token, sig_cfg["host"], int(sig_cfg["port"]),
                                  sig_cfg["path"], sig_cfg["key"], bool(sig_cfg["secure"]))
            sig.on_state = self._on_signal_state
            adapter = PeerJSAdapter(sig, self._ice_servers)
            adapter.on_data_connection = self._on_data_connection
            try:
                try:
                    await sig.start()
                except asyncio.CancelledError:  # app fechado durante a tentativa: libera a sessão HTTP
                    await sig.close()
                    raise
                self.signaling, self.adapter = sig, adapter
                self.ui.status("available", "Disponível para acesso remoto — aguardando conexão")
                return
            except SignalingError as exc:
                await sig.close()
                if exc.code == "ID-TAKEN":
                    new = self.pm.regenerate_device_id()
                    self.ui.event(f"ID em uso por outro computador; novo ID gerado: {display(new)}")
                    self.ui.status("connecting", "Novo ID gerado, registrando...")
                    continue
                # erro definitivo (ex.: chave inválida): para de tentar até o usuário corrigir/reiniciar
                self.ui.status("error", f"{exc}. Corrija em Configurações → Servidor.")
                return
            except Exception as exc:  # noqa: BLE001
                await sig.close()
                self.ui.status("connecting", f"Servidor de sinalização indisponível ({sig_cfg['host']}:{sig_cfg['port']}). "
                                             f"Nova tentativa automática em {int(backoff)} s")
                log.warning("falha ao conectar na sinalização: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30)

    def _on_signal_state(self, state: str, detail: str) -> None:
        if self.paused:
            return
        if state == "connected":
            self.ui.status("available", "Disponível para acesso remoto — aguardando conexão")
        elif state == "reconnecting":
            self.ui.status("connecting", "Reconectando ao servidor de sinalização...")

    async def _disconnect_signaling(self) -> None:
        if self.adapter:
            await self.adapter.close_all()
        if self.signaling:
            await self.signaling.close()
        self.signaling = self.adapter = None

    async def pause(self) -> None:
        self.paused = True
        await self.end_session("serviço pausado pelo usuário")
        await self._disconnect_signaling()
        self.ui.status("paused", "Serviço pausado — computador indisponível para acesso")

    async def resume(self) -> None:
        if not self.paused and self.signaling:
            return
        self.paused = False
        await self._connect_signaling()

    async def restart(self) -> None:
        """Reconecta com novas configurações ou novo ID."""
        await self.end_session("reinício do serviço")
        await self._disconnect_signaling()
        self.paused = False
        await self._connect_signaling()

    async def _shutdown(self) -> None:
        await self.end_session("aplicativo encerrado")
        await self._disconnect_signaling()
        if self._capturer:
            self._capturer.close()

    async def _ice_servers(self):
        return await fetch_ice_servers(self.settings.ice_endpoint_url(), self.settings.get("ice_servers"))

    def _on_password_changed(self) -> None:
        if self.loop is None:
            return
        s = self.session
        if s and s.state in ("await-proof", "await-local"):
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(
                self._close_session(s, "senha alterada durante a autenticação", notify="password-changed")))

    # ============================================================ conexões
    def _on_data_connection(self, conn: DataConn) -> None:
        if self.session is not None and self.session.state != "closed":
            log.info("Conexão de %s recusada: já existe uma sessão", conn.remote_id)
            asyncio.ensure_future(self._reject_busy(conn))
            return
        s = Session(conn=conn)
        self.session = s
        conn.on_message = lambda obj: self._on_message(s, obj)
        conn.on_close = lambda _c, reason: asyncio.ensure_future(self._close_session(s, reason, notify=None))
        s.tasks.append(asyncio.ensure_future(self._watchdog(s)))
        self.ui.event(f"Solicitação de conexão recebida de {conn.remote_id}")

    async def _reject_busy(self, conn: DataConn) -> None:
        try:
            await asyncio.wait_for(conn.opened.wait(), 10)
            conn.send({"t": "busy"})
            await asyncio.sleep(0.5)
        except asyncio.TimeoutError:
            pass
        await conn.close("ocupado")

    async def _watchdog(self, s: Session) -> None:
        start = time.monotonic()
        while s.state != "closed":
            await asyncio.sleep(1)
            if s.state == "await-hello" and time.monotonic() - start > HELLO_TIMEOUT_S:
                await self._close_session(s, "sem hello", notify="timeout")
            elif s.state in ("starting", "active") and time.monotonic() - s.last_seen > IDLE_TIMEOUT_S:
                await self._close_session(s, "operador sem resposta (conexão perdida)", notify="timeout")

    # ============================================================ mensagens
    def _on_message(self, s: Session, msg: Any) -> None:
        if not isinstance(msg, dict) or s.state == "closed":
            return
        s.last_seen = time.monotonic()
        t = msg.get("t")
        try:
            if t == "ping":
                s.conn.send({"t": "pong", "ts": msg.get("ts")})
            elif t == "hello" and s.state == "await-hello":
                self._on_hello(s, msg)
            elif t == "proof" and s.state == "await-proof":
                self._on_proof(s, msg)
            elif t == "media-answer" and s.state in ("starting", "active") and s.media_fp_ctrl:
                if not s.media_fp_ctrl.done():
                    s.media_fp_ctrl.set_result(str(msg.get("fp", ""))[:200])
            elif t in ("m", "k", "sc") and s.state == "active":
                self._on_input(s, msg)
            elif t == "quality" and s.state == "active" and s.track:
                lvl = msg.get("level")
                if lvl in ("auto", "low", "medium", "high"):
                    s.track.set_quality(lvl)
                elif msg.get("hint") in ("up", "down"):
                    s.track.suggest(msg["hint"])
            elif t == "bye":
                asyncio.ensure_future(self._close_session(s, "operador desconectou", notify=None))
            # mensagens desconhecidas ou fora de ordem são ignoradas
        except Exception:  # noqa: BLE001
            log.exception("erro tratando mensagem %s", t)

    def _on_hello(self, s: Session, msg: dict) -> None:
        now = time.monotonic()
        self._hello_times = [x for x in self._hello_times if now - x < 60] + [now]
        if len(self._hello_times) > 20:
            s.conn.send({"t": "auth-fail", "reason": "rate-limit"})
            asyncio.ensure_future(self._close_session(s, "excesso de tentativas", notify=None))
            return
        if msg.get("v") != PROTOCOL_VERSION:
            s.conn.send({"t": "auth-fail", "reason": "version"})
            asyncio.ensure_future(self._close_session(s, "versão de protocolo incompatível", notify=None))
            return
        s.operator = safe_text(msg.get("name"), 40)
        try:
            hs = AuthHandshake(self.pm, self.limiter, s.conn.local_fp, s.conn.remote_fp)
            challenge = hs.challenge(msg)
        except AuthError as exc:
            self._auth_failed(s, exc)
            return
        s.handshake = hs
        s.state = "await-proof"
        s.conn.send(challenge)

    def _on_proof(self, s: Session, msg: dict) -> None:
        try:
            ok = s.handshake.verify(msg)
        except AuthError as exc:
            self._auth_failed(s, exc)
            return
        s.credential = s.handshake.credential_kind or "-"
        s.state = "await-local"
        s.conn.send(ok)
        s.conn.send({"t": "auth-wait-local"})
        self.ui.event(f"Senha validada para '{s.operator}'. Aguardando autorização local.")
        # Adianta a preparação da mídia (captura + coleta de ICE) enquanto o usuário decide.
        # Nada é enviado ao navegador antes da autorização: a oferta fica retida e a trilha bloqueada.
        s.prepared = asyncio.ensure_future(self._prepare_media(s))
        s.tasks.append(s.prepared)
        s.tasks.append(asyncio.ensure_future(self._ask_local(s)))

    async def _prepare_media(self, s: Session) -> MediaCall:
        loop = asyncio.get_running_loop()
        if self._capturer is None:
            self._capturer = await loop.run_in_executor(
                None, lambda: ScreenCapturer(self.settings.get("video", "monitor")))
        s.track = ScreenVideoTrack(self._capturer, self.settings.get("video", "quality"))
        s.call = await self.adapter.prepare_call(s.conn.remote_id, s.track)
        s.call.on_close = lambda _c, reason: asyncio.ensure_future(
            self._close_session(s, f"mídia encerrada ({reason})", notify="media"))
        return s.call

    def _auth_failed(self, s: Session, exc: AuthError) -> None:
        reason = exc.reason
        msg = {"t": "auth-fail", "reason": reason}
        msg.update({k: v for k, v in exc.extra.items() if k in ("remaining", "locked_for")})
        s.conn.send(msg)
        text = {"wrong-password": "Senha incorreta", "locked": "Bloqueado por excesso de tentativas",
                "channel-binding": "Falha de vínculo do canal (possível interceptação)",
                "replay": "Desafio reutilizado", "timeout": "Tempo de autenticação esgotado",
                "password-changed": "Senha alterada durante a autenticação",
                "no-password": "Nenhuma senha ativa"}.get(reason, "Requisição inválida")
        if exc.extra.get("locked_for"):
            text += f" — bloqueio de {exc.extra['locked_for']} s"
        log.warning("Autenticação recusada para %s: %s", s.conn.remote_id, reason)
        self.ui.event(f"Tentativa de acesso recusada ({text}) — {s.conn.remote_id}")
        asyncio.ensure_future(self._close_session(s, f"autenticação recusada: {reason}", notify=None, delay=0.5))

    async def _ask_local(self, s: Session) -> None:
        route = describe_route(s.conn.pc)
        request = {"operator": s.operator, "peer": s.conn.remote_id, "ip": route.get("remote_ip", "-"),
                   "relay": route.get("relay", False), "credential": s.credential}
        timeout = self.settings.get("authorization_timeout_s") or LOCAL_DECISION_DEFAULT_S
        try:
            accepted, permission = await asyncio.wait_for(self.ui.authorize(request), timeout)
        except asyncio.TimeoutError:
            accepted, permission = False, "view"
            self.ui.event("Solicitação expirou sem resposta do usuário local")
        if s.state != "await-local":
            return
        if not accepted:
            s.conn.send({"t": "rejected"})
            self.ui.event(f"Acesso de '{s.operator}' rejeitado pelo usuário local")
            await self._close_session(s, "rejeitado pelo usuário local", notify=None, delay=0.5)
            return
        s.permission = permission if permission in PERMISSIONS else "view"
        await self._start_media(s)

    async def _start_media(self, s: Session) -> None:
        s.state = "starting"
        try:
            call = await asyncio.wait_for(s.prepared, 30)
        except Exception as exc:  # noqa: BLE001
            log.error("falha ao preparar a mídia: %r", exc)
            await self._close_session(s, "falha ao preparar a transmissão", notify="media")
            return
        if s.state != "starting":
            return
        cap = self._capturer
        mon = cap.monitor()
        virt = await asyncio.get_running_loop().run_in_executor(None, cap.virtual_screen)
        s.mouse = MouseController(mon, virt)
        s.keyboard = KeyboardController()
        s.bucket = TokenBucket(self.settings.get("security", "max_input_events_per_s"))
        s.media_fp_ctrl = asyncio.get_running_loop().create_future()
        s.conn.send({"t": "authorized", "permission": s.permission, "session": s.sid,
                     "screen": {"width": mon.width, "height": mon.height}})
        try:
            await self.adapter.send_call(call, {"session": s.sid})
            s.conn.send({"t": "media-offer", "connectionId": call.connection_id, "fp": call.local_fp})
            fp_ctrl = await asyncio.wait_for(s.media_fp_ctrl, MEDIA_BIND_TIMEOUT_S)
            await asyncio.wait_for(s.call.remote_ready.wait(), MEDIA_BIND_TIMEOUT_S)
            if fp_ctrl != s.call.remote_fp:
                log.error("Fingerprint da mídia não confere: possível interceptação. Sessão encerrada.")
                self.ui.event("Sessão encerrada: vínculo da mídia inválido (possível interceptação)")
                await self._close_session(s, "vínculo da mídia inválido", notify="media-binding")
                return
        except asyncio.TimeoutError:
            await self._close_session(s, "navegador não aceitou a mídia a tempo", notify="timeout")
            return
        if s.state != "starting":
            return
        s.track.gate.set()
        s.state = "active"
        route = describe_route(s.conn.pc)
        info = {"operator": s.operator, "peer": s.conn.remote_id, "since": s.started_at,
                "permission": s.permission, "ip": route.get("remote_ip", "-"),
                "relay": route.get("relay", False)}
        self.ui.session(info)
        self.ui.status("session", f"Sessão remota ativa com '{s.operator}'")
        log.info("Sessão ativa: operador=%s permissão=%s rota=%s", s.operator, s.permission, route)

    # ============================================================ entrada remota
    def _on_input(self, s: Session, msg: dict) -> None:
        if s.permission == "view":
            return
        if not s.bucket.allow():
            return
        t = msg["t"]
        try:
            if t == "m":
                a = msg.get("a")
                if a == "move":
                    s.mouse.move(float(msg["x"]), float(msg["y"]))
                elif a in ("down", "up"):
                    x, y = msg.get("x"), msg.get("y")
                    s.mouse.button(int(msg["b"]), a == "down",
                                   float(x) if x is not None else None, float(y) if y is not None else None)
                elif a == "wheel":
                    s.mouse.wheel(int(msg.get("dx", 0)), int(msg.get("dy", 0)))
            elif s.permission == "full" and t == "k":
                s.keyboard.key(str(msg.get("code", "")), str(msg.get("key", "")), msg.get("a") == "down")
            elif s.permission == "full" and t == "sc" and msg.get("name") in SHORTCUTS:
                s.keyboard.shortcut(msg["name"])
        except (ValueError, KeyError, TypeError):
            pass  # comando inválido: descartado
        except OSError:
            now = time.monotonic()
            if now - s.uipi_warned > 5:
                s.uipi_warned = now
                s.conn.send({"t": "warn", "code": "input-blocked",
                             "text": "O Windows bloqueou a entrada (janela de administrador ou tela segura)."})

    # ============================================================ encerramento
    async def end_session(self, reason: str = "encerrada pelo usuário local") -> None:
        if self.session and self.session.state != "closed":
            await self._close_session(self.session, reason, notify="local")

    async def _close_session(self, s: Session, reason: str, notify: str | None, delay: float = 0) -> None:
        if s.state == "closed":
            return
        was_active = s.state in ("starting", "active")
        s.state = "closed"
        for mod in (s.keyboard, s.mouse):
            if mod:
                try:
                    mod.release_all()
                except Exception:  # noqa: BLE001
                    pass
        if notify:
            s.conn.send({"t": "bye", "reason": notify})
        if delay:
            await asyncio.sleep(delay)
        if s.track:
            s.track.stop()
        for t in s.tasks:
            if t is not asyncio.current_task():
                t.cancel()
        if s.call:
            await s.call.close("fim da sessão")
        await s.conn.close("fim da sessão")
        if self.session is s:
            self.session = None
        log.info("Sessão encerrada: %s", reason)
        if was_active:
            self.ui.event(f"Sessão encerrada: {reason}")
            self.ui.session(None)
        if not self.paused and self.signaling:
            self.ui.status("available", "Disponível para acesso remoto — aguardando conexão")
