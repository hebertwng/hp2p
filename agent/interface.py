"""Interface gráfica do agente (PySide6).

A rede roda num loop asyncio em thread própria (AgentService). Esta camada só exibe estado e envia
comandos; a comunicação entre as threads usa sinais Qt (thread → GUI) e
asyncio.run_coroutine_threadsafe / call_soon_threadsafe (GUI → thread de rede).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QSystemTrayIcon,
                               QTabWidget, QVBoxLayout, QWidget, QMenu)

from config import APP_NAME, APP_VERSION, FROZEN, Settings
from identity import display
from password_manager import PasswordManager, generate_password, validate_custom
from session_manager import AgentService

log = logging.getLogger("hp2p.ui")

PERM_LABEL = {"view": "Somente visualizar a tela", "mouse": "Visualizar e controlar o mouse",
              "full": "Visualizar e controlar mouse e teclado"}
STATUS_COLOR = {"starting": "#8a8f98", "connecting": "#d18b00", "available": "#1a9a55",
                "session": "#c62828", "paused": "#6b7280", "error": "#c62828"}


def make_icon(color: str = "#1f5bd8") -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", 15, QFont.Bold)
    p.setFont(f)
    p.drawText(QRect(0, 0, 64, 64), Qt.AlignCenter, "HP2P")
    p.end()
    return QIcon(pm)


# =============================================================== ponte rede <-> GUI
class QtBridge(QObject):
    statusChanged = Signal(str, str)
    sessionChanged = Signal(object)
    eventLogged = Signal(str)
    authRequested = Signal(int, object)
    authCancelled = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._req = 0
        self._futures: dict[int, asyncio.Future] = {}

    # chamados na thread de rede ---------------------------------------
    def status(self, state: str, text: str) -> None:
        self.statusChanged.emit(state, text)

    def session(self, info: dict | None) -> None:
        self.sessionChanged.emit(info)

    def event(self, text: str) -> None:
        log.info("evento: %s", text)
        self.eventLogged.emit(text)

    async def authorize(self, request: dict) -> tuple[bool, str]:
        self._req += 1
        rid = self._req
        fut = asyncio.get_running_loop().create_future()
        self._futures[rid] = fut
        self.authRequested.emit(rid, request)
        try:
            return await fut
        finally:
            self._futures.pop(rid, None)
            if not fut.done() or fut.cancelled():
                self.authCancelled.emit(rid)

    # chamado na thread da GUI -----------------------------------------
    def resolve(self, rid: int, accepted: bool, permission: str) -> None:
        fut = self._futures.get(rid)
        if fut and self.loop:
            self.loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result((accepted, permission)))


class ServiceThread(threading.Thread):
    def __init__(self, service: AgentService, bridge: QtBridge):
        super().__init__(name="hp2p-network", daemon=True)
        self.service = service
        self.bridge = bridge
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bridge.loop = self.loop
        self.ready.set()
        try:
            self.loop.run_until_complete(self.service.run())
        except Exception:  # noqa: BLE001
            log.exception("serviço de rede terminou com erro")
        finally:
            # cancela tarefas ainda pendentes (ex.: "Iniciar serviço" em andamento) antes de fechar o loop
            pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def submit(self, coro) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.service.stop)


# =============================================================== janelas
class AuthorizationDialog(QDialog):
    def __init__(self, request: dict, timeout_s: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Solicitação de acesso remoto — HP2P")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(True)
        self.remaining = timeout_s
        lay = QVBoxLayout(self)
        title = QLabel("Um operador deseja acessar este computador.")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)
        form = QFormLayout()
        form.addRow("Operador:", QLabel(f"<b>{_esc(request.get('operator'))}</b>"))
        form.addRow("Origem (IP):", QLabel(_esc(request.get("ip")) + ("  (via TURN)" if request.get("relay") else "")))
        form.addRow("ID do controlador:", QLabel(_esc(request.get("peer"))))
        cred = {"auto": "senha de acesso", "custom": "senha personalizada", "temp": "senha temporária"}
        form.addRow("Autenticado com:", QLabel(cred.get(request.get("credential"), "-") + " ✔"))
        lay.addLayout(form)
        box = QGroupBox("Permissões desta sessão")
        bl = QVBoxLayout(box)
        self.group = QButtonGroup(self)
        for i, key in enumerate(("view", "mouse", "full")):
            rb = QRadioButton(PERM_LABEL[key])
            rb.setProperty("perm", key)
            self.group.addButton(rb, i)
            bl.addWidget(rb)
            if key == "full":
                rb.setChecked(True)
        lay.addWidget(box)
        self.countdown = QLabel()
        self.countdown.setStyleSheet("color: #6b7280;")
        lay.addWidget(self.countdown)
        buttons = QHBoxLayout()
        self.reject_btn = QPushButton("Rejeitar conexão")
        self.accept_btn = QPushButton("Aceitar conexão")
        self.accept_btn.setStyleSheet("background:#1a9a55; color:white; font-weight:600; padding:8px 16px;")
        self.reject_btn.setStyleSheet("padding:8px 16px;")
        buttons.addWidget(self.reject_btn)
        buttons.addStretch()
        buttons.addWidget(self.accept_btn)
        lay.addLayout(buttons)
        self.reject_btn.clicked.connect(self.reject)
        self.accept_btn.clicked.connect(self.accept)
        self.reject_btn.setDefault(True)  # Enter acidental não aceita a conexão
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self) -> None:
        self.countdown.setText(f"Rejeição automática em {self.remaining} s")
        self.remaining -= 1
        if self.remaining < 0:
            self.reject()

    def permission(self) -> str:
        b = self.group.checkedButton()
        return b.property("perm") if b else "view"


class SessionBanner(QWidget):
    """Indicador permanente de sessão ativa (sempre visível, topo da tela)."""

    def __init__(self, on_end):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("QWidget#b{background:#c62828; border-radius:0 0 10px 10px;} "
                           "QLabel{color:white; font-weight:600;} "
                           "QPushButton{background:white; color:#c62828; border-radius:6px; padding:3px 10px; font-weight:600;}")
        frame = QFrame(self)
        frame.setObjectName("b")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 6, 8, 6)
        self.label = QLabel()
        btn = QPushButton("Encerrar")
        btn.clicked.connect(on_end)
        lay.addWidget(self.label)
        lay.addWidget(btn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

    def show_for(self, info: dict) -> None:
        kind = "Acesso NÃO SUPERVISIONADO" if info.get("unattended") else "Sessão remota ativa"
        self.label.setText(f"● {kind} — {_esc(info.get('operator'))} "
                           f"({PERM_LABEL.get(info.get('permission'), '')})")
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(QPoint(geo.center().x() - self.width() // 2, geo.top()))
        self.show()
        self.raise_()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, pm: PasswordManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.pm = pm
        self.setWindowTitle("Configurações — HP2P")
        self.setMinimumWidth(480)
        tabs = QTabWidget()

        # Geral ------------------------------------------------------
        g = QWidget()
        gl = QVBoxLayout(g)
        self.startup = QCheckBox("Iniciar com o Windows")
        self.startup.setChecked(bool(settings.get("start_with_windows")))
        self.minimized = QCheckBox("Iniciar minimizado (na bandeja)")
        self.minimized.setChecked(bool(settings.get("start_minimized")))
        self.auto_pw = QCheckBox("Gerar senha automaticamente a cada início")
        self.auto_pw.setChecked(pm.mode == "auto")
        gl.addWidget(self.startup)
        gl.addWidget(self.minimized)
        gl.addWidget(self.auto_pw)
        gl.addStretch()
        tabs.addTab(g, "Geral")

        # Senha ------------------------------------------------------
        s = QWidget()
        sl = QVBoxLayout(s)
        cbox = QGroupBox("Senha personalizada (fixa)")
        cl = QFormLayout(cbox)
        self.custom1 = QLineEdit(echoMode=QLineEdit.Password)
        self.custom2 = QLineEdit(echoMode=QLineEdit.Password)
        cl.addRow("Nova senha:", self.custom1)
        cl.addRow("Confirmar:", self.custom2)
        sb = QPushButton("Definir senha personalizada")
        sb.clicked.connect(self._set_custom)
        cl.addRow(sb)
        cl.addRow(QLabel("<small>Armazenada apenas como verificador (Argon2id + SRP), protegido por DPAPI. "
                         "Não pode ser exibida depois de definida.</small>"))
        sl.addWidget(cbox)
        tbox = QGroupBox("Senha temporária para suporte")
        tl = QFormLayout(tbox)
        self.temp = QLineEdit(generate_password(14))
        self.temp_min = QSpinBox(minimum=5, maximum=1440, value=60, suffix=" min")
        tl.addRow("Senha:", self.temp)
        tl.addRow("Validade:", self.temp_min)
        trow = QHBoxLayout()
        tb = QPushButton("Ativar senha temporária")
        tb.clicked.connect(self._set_temp)
        tc = QPushButton("Remover")
        tc.clicked.connect(self._clear_temp)
        trow.addWidget(tb)
        trow.addWidget(tc)
        tl.addRow(trow)
        sl.addWidget(tbox)
        sl.addStretch()
        tabs.addTab(s, "Senha")

        # Acesso não supervisionado ------------------------------------
        u = QWidget()
        ul = QVBoxLayout(u)
        info = QLabel("Permite conectar <b>sem</b> que alguém clique em \"Aceitar conexão\" neste computador, "
                      "usando uma senha própria. A faixa vermelha de sessão ativa continua aparecendo e "
                      "pode encerrar o acesso a qualquer momento.<br><br>"
                      "Funciona enquanto este usuário estiver logado e o HP2P aberto — ative "
                      "\"Iniciar com o Windows\" na aba Geral.")
        info.setWordWrap(True)
        ul.addWidget(info)
        self.un_status = QLabel()
        ul.addWidget(self.un_status)
        ubox = QGroupBox("Senha de acesso não supervisionado")
        uf = QFormLayout(ubox)
        self.un1 = QLineEdit(echoMode=QLineEdit.Password)
        self.un2 = QLineEdit(echoMode=QLineEdit.Password)
        self.un1.setPlaceholderText("mínimo 12 caracteres, 3 tipos")
        uf.addRow("Senha:", self.un1)
        uf.addRow("Confirmar:", self.un2)
        self.un_perm = QComboBox()
        for k in ("view", "mouse", "full"):
            self.un_perm.addItem(PERM_LABEL[k], k)
        self.un_perm.setCurrentIndex(max(0, self.un_perm.findData(settings.get("unattended_permission"))))
        uf.addRow("Permissões:", self.un_perm)
        urow = QHBoxLayout()
        self.un_set = QPushButton("Ativar / trocar senha")
        self.un_set.clicked.connect(self._set_unattended)
        self.un_off = QPushButton("Desativar")
        self.un_off.clicked.connect(self._clear_unattended)
        urow.addWidget(self.un_set)
        urow.addWidget(self.un_off)
        uf.addRow(urow)
        ul.addWidget(ubox)
        ul.addStretch()
        tabs.addTab(u, "Não supervisionado")
        self._refresh_unattended()

        # Servidor ---------------------------------------------------
        v = QWidget()
        vl = QFormLayout(v)
        sig = settings.get("signaling")
        self.host = QLineEdit(sig["host"])
        self.port = QSpinBox(minimum=1, maximum=65535, value=int(sig["port"]))
        self.path = QLineEdit(sig["path"])
        self.key = QLineEdit(sig["key"])
        self.secure = QCheckBox("Usar HTTPS/WSS (TLS)")
        self.secure.setChecked(bool(sig["secure"]))
        vl.addRow("Servidor de sinalização:", self.host)
        vl.addRow("Porta:", self.port)
        vl.addRow("Caminho:", self.path)
        vl.addRow("Chave do PeerServer:", self.key)
        vl.addRow("", self.secure)
        self.ice_ep = QLineEdit(settings.get("ice_endpoint") or "")
        self.ice_ep.setPlaceholderText("automático: https://servidor:porta/hp2p/ice")
        vl.addRow("Endpoint de ICE/TURN:", self.ice_ep)
        tabs.addTab(v, "Servidor")

        # STUN/TURN --------------------------------------------------
        t = QWidget()
        tl2 = QVBoxLayout(t)
        tl2.addWidget(QLabel("Servidores ICE usados se o servidor não fornecer credenciais TURN temporárias (JSON):"))
        self.ice = QPlainTextEdit(json.dumps(settings.get("ice_servers"), indent=2))
        tl2.addWidget(self.ice)
        tabs.addTab(t, "STUN/TURN")

        # Vídeo ------------------------------------------------------
        vd = QWidget()
        vdl = QFormLayout(vd)
        self.monitor = QSpinBox(minimum=1, maximum=8, value=int(settings.get("video", "monitor")))
        self.quality = QComboBox()
        for k, lbl in (("auto", "Automática"), ("low", "Baixa"), ("medium", "Média"), ("high", "Alta")):
            self.quality.addItem(lbl, k)
        self.quality.setCurrentIndex(max(0, self.quality.findData(settings.get("video", "quality"))))
        vdl.addRow("Monitor capturado:", self.monitor)
        vdl.addRow("Qualidade inicial:", self.quality)
        tabs.addTab(vd, "Vídeo")

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Salvar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(bb)
        self.server_changed = False

    def _set_custom(self) -> None:
        a, b = self.custom1.text(), self.custom2.text()
        if a != b:
            QMessageBox.warning(self, APP_NAME, "As senhas não conferem.")
            return
        err = validate_custom(a)
        if err:
            QMessageBox.warning(self, APP_NAME, err)
            return
        self.pm.set_custom(a)
        self.custom1.clear()
        self.custom2.clear()
        self.auto_pw.setChecked(False)
        QMessageBox.information(self, APP_NAME, "Senha personalizada definida. A senha anterior foi invalidada.")

    def _set_temp(self) -> None:
        try:
            self.pm.set_temporary(self.temp.text(), self.temp_min.value())
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        QApplication.clipboard().setText(self.temp.text())
        QMessageBox.information(self, APP_NAME, f"Senha temporária ativa por {self.temp_min.value()} min "
                                                "(copiada para a área de transferência).")

    def _refresh_unattended(self) -> None:
        on = self.pm.unattended_enabled
        self.un_status.setText("<b style='color:#c62828'>● ATIVO</b> — conexões com a senha não supervisionada "
                               "entram sem aceite." if on else "<b>○ Desativado</b>")
        self.un_off.setEnabled(on)

    def _set_unattended(self) -> None:
        a, b = self.un1.text(), self.un2.text()
        if a != b:
            QMessageBox.warning(self, APP_NAME, "As senhas não conferem.")
            return
        try:
            self.pm.set_unattended(a)
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        self.settings.set(self.un_perm.currentData(), "unattended_permission")
        self.un1.clear()
        self.un2.clear()
        self._refresh_unattended()
        msg = "Acesso não supervisionado ativado. A senha anterior (se havia) foi invalidada."
        if not self.startup.isChecked():
            if ask_yes_no(self, APP_NAME, msg + "\n\nPara funcionar após reiniciar o computador, o HP2P "
                                    "precisa iniciar com o Windows. Ativar agora?"):
                self.startup.setChecked(True)
                self.minimized.setChecked(True)
        else:
            QMessageBox.information(self, APP_NAME, msg)

    def _clear_unattended(self) -> None:
        if ask_yes_no(self, APP_NAME, "Desativar o acesso não supervisionado? A senha será apagada.") :
            self.pm.clear_unattended()
            self._refresh_unattended()

    def _clear_temp(self) -> None:
        self.pm.clear_temporary()

    def _save(self) -> None:
        try:
            ice = json.loads(self.ice.toPlainText() or "[]")
            assert isinstance(ice, list)
        except (json.JSONDecodeError, AssertionError):
            QMessageBox.warning(self, APP_NAME, "JSON de servidores ICE inválido.")
            return
        st = self.settings
        new_sig = {"host": self.host.text().strip(), "port": self.port.value(), "path": self.path.text().strip() or "/signal",
                   "key": self.key.text().strip() or "hp2p", "secure": self.secure.isChecked()}
        self.server_changed = (new_sig != st.get("signaling") or ice != st.get("ice_servers")
                               or self.ice_ep.text().strip() != (st.get("ice_endpoint") or ""))
        st.data["signaling"] = new_sig
        st.data["ice_servers"] = ice
        st.data["ice_endpoint"] = self.ice_ep.text().strip()
        st.data["start_minimized"] = self.minimized.isChecked()
        st.data["start_with_windows"] = self.startup.isChecked()
        st.data["video"]["monitor"] = self.monitor.value()
        st.data["video"]["quality"] = self.quality.currentData()
        st.data["unattended_permission"] = self.un_perm.currentData()
        want_auto = self.auto_pw.isChecked()
        st.data["auto_password"] = want_auto
        st.save()
        set_startup(self.startup.isChecked())
        if want_auto and self.pm.mode != "auto":
            self.pm.use_auto()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, pm: PasswordManager, bridge: QtBridge, thread: ServiceThread):
        super().__init__()
        self.settings, self.pm, self.bridge, self.thread = settings, pm, bridge, thread
        self.service = thread.service
        self.setWindowTitle(f"{APP_NAME} — Acesso Remoto")
        self.setWindowIcon(make_icon())
        self.setMinimumWidth(460)
        self._quitting = False
        self._dialogs: dict[int, AuthorizationDialog] = {}
        self.banner = SessionBanner(self._end_session)
        self.session_info = None

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setSpacing(12)

        # ID
        idbox = QGroupBox("IDENTIFICAÇÃO DO COMPUTADOR")
        il = QHBoxLayout(idbox)
        self.id_label = QLabel()
        self.id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.id_label.setStyleSheet("font-size: 28px; font-weight: 700; letter-spacing: 2px;")
        copy_id = QPushButton("Copiar")
        copy_id.clicked.connect(lambda: self._copy(display(self.pm.device_id), "ID copiado"))
        new_id = QPushButton("Novo ID")
        new_id.clicked.connect(self._new_id)
        il.addWidget(self.id_label, 1)
        il.addWidget(copy_id)
        il.addWidget(new_id)
        lay.addWidget(idbox)

        # Senha
        pwbox = QGroupBox("SENHA DE ACESSO")
        pl = QGridLayout(pwbox)
        self.pw_edit = QLineEdit(readOnly=True, echoMode=QLineEdit.Password)
        self.pw_edit.setStyleSheet("font-size: 18px; font-family: Consolas, monospace; padding: 4px;")
        self.view_btn = QPushButton("Visualizar")
        self.view_btn.setCheckable(True)
        self.view_btn.toggled.connect(self._toggle_pw)
        self.copy_pw = QPushButton("Copiar")
        self.copy_pw.clicked.connect(lambda: self._copy(self.pm.visible_password or "", "Senha copiada"))
        self.new_pw = QPushButton("Gerar nova senha")
        self.new_pw.clicked.connect(self._new_password)
        self.pw_note = QLabel()
        self.pw_note.setStyleSheet("color: #6b7280;")
        self.pw_note.setWordWrap(True)
        pl.addWidget(self.pw_edit, 0, 0, 1, 2)
        pl.addWidget(self.view_btn, 0, 2)
        pl.addWidget(self.copy_pw, 0, 3)
        pl.addWidget(self.new_pw, 1, 0)
        pl.addWidget(self.pw_note, 1, 1, 1, 3)
        lay.addWidget(pwbox)

        # Status
        stbox = QGroupBox("STATUS DO APLICATIVO")
        sl = QVBoxLayout(stbox)
        self.status_label = QLabel("Aplicativo iniciado.")
        self.status_label.setWordWrap(True)
        self.events = QListWidget()
        self.events.setMaximumHeight(110)
        sl.addWidget(self.status_label)
        sl.addWidget(self.events)
        lay.addWidget(stbox)

        # Sessão
        sebox = QGroupBox("SESSÃO REMOTA")
        sel = QVBoxLayout(sebox)
        self.session_label = QLabel("Nenhuma conexão ativa.")
        self.session_label.setWordWrap(True)
        self.session_label.setMinimumHeight(self.session_label.fontMetrics().lineSpacing() * 4 + 4)
        self.session_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        sel.addWidget(self.session_label)
        lay.addWidget(sebox)

        # Botões
        row = QHBoxLayout()
        self.start_btn = QPushButton("Iniciar serviço")
        self.pause_btn = QPushButton("Pausar serviço")
        self.end_btn = QPushButton("Encerrar conexão")
        self.end_btn.setStyleSheet("QPushButton:enabled{background:#c62828; color:white; font-weight:600;}")
        cfg_btn = QPushButton("Configurações")
        self.start_btn.clicked.connect(lambda: self.thread.submit(self.service.resume()))
        self.pause_btn.clicked.connect(lambda: self.thread.submit(self.service.pause()))
        self.end_btn.clicked.connect(self._end_session)
        cfg_btn.clicked.connect(self._settings)
        for b in (self.start_btn, self.pause_btn, self.end_btn, cfg_btn):
            row.addWidget(b)
        lay.addLayout(row)
        foot = QLabel(f"<small>{APP_NAME} {APP_VERSION} · conexões P2P criptografadas (DTLS/SRTP) · "
                      "autenticação SRP-6a</small>")
        foot.setStyleSheet("color:#8a8f98;")
        lay.addWidget(foot)
        self.setCentralWidget(root)

        # Bandeja
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        a_show = QAction("Abrir HP2P", self)
        a_show.triggered.connect(self._show)
        a_end = QAction("Encerrar conexão", self)
        a_end.triggered.connect(self._end_session)
        a_quit = QAction("Sair", self)
        a_quit.triggered.connect(self.quit)
        menu.addAction(a_show)
        menu.addAction(a_end)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self._show())
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

        bridge.statusChanged.connect(self._on_status)
        bridge.sessionChanged.connect(self._on_session)
        bridge.eventLogged.connect(self._on_event)
        bridge.authRequested.connect(self._on_auth)
        bridge.authCancelled.connect(self._on_auth_cancel)
        pm.listeners.append(lambda: QTimer.singleShot(0, self._refresh_password))
        self._refresh_id()
        self._refresh_password()
        self._on_session(None)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_session_time)
        self._tick.start(1000)

    # ---------------------------------------------------------------- helpers
    def _copy(self, text: str, msg: str) -> None:
        QApplication.clipboard().setText(text)
        self._on_event(msg)

    def _refresh_id(self) -> None:
        self.id_label.setText(display(self.pm.device_id))

    def _refresh_password(self) -> None:
        pw = self.pm.visible_password
        if pw:
            self.pw_edit.setText(pw)
            self.view_btn.setEnabled(True)
            self.copy_pw.setEnabled(True)
            note = "Senha automática (nova a cada início)."
        else:
            self.view_btn.setChecked(False)
            self.pw_edit.setText("••••••••••••")
            self.view_btn.setEnabled(False)
            self.copy_pw.setEnabled(False)
            note = "Senha personalizada definida (não pode ser exibida)."
        exp = self.pm.temporary_expires_at()
        if exp:
            note += f"  Temporária válida até {datetime.fromtimestamp(exp):%H:%M}."
        if self.pm.unattended_enabled:
            note += "  <b style='color:#c62828'>Acesso não supervisionado ATIVO.</b>"
        self.pw_note.setText(note)

    def _toggle_pw(self, on: bool) -> None:
        self.pw_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        self.view_btn.setText("Ocultar" if on else "Visualizar")

    def _new_password(self) -> None:
        if self.pm.mode == "custom":
            if ask_yes_no(self, APP_NAME, "Trocar a senha personalizada por uma senha automática?") is False:
                return
            self.pm.use_auto()
            self.settings.set(True, "auto_password")
        else:
            self.pm.regenerate_auto()
        self._on_event("Nova senha gerada. A senha anterior foi invalidada.")

    def _new_id(self) -> None:
        if ask_yes_no(self, APP_NAME, "Gerar uma nova identificação? Quem usa o ID atual não "
                                "conseguirá mais localizar este computador.") is False:
            return
        self.pm.regenerate_device_id()
        self._refresh_id()
        self.thread.submit(self.service.restart())

    def _settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.pm, self)
        if dlg.exec() == QDialog.Accepted and dlg.server_changed:
            self._on_event("Configuração do servidor alterada; reconectando...")
            self.thread.submit(self.service.restart())
        self._refresh_password()

    def _end_session(self) -> None:
        self.thread.submit(self.service.end_session("encerrada pelo usuário local"))

    def _show(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------------------- sinais
    def _on_status(self, state: str, text: str) -> None:
        color = STATUS_COLOR.get(state, "#8a8f98")
        self.status_label.setText(f"<span style='color:{color}; font-size:16px;'>●</span> {_esc(text)}")
        self.start_btn.setEnabled(state in ("paused", "error"))
        self.pause_btn.setEnabled(state in ("available", "session", "connecting"))
        self.tray.setToolTip(f"{APP_NAME} — {text}")
        self._refresh_id()

    def _on_event(self, text: str) -> None:
        self.events.insertItem(0, f"{datetime.now():%H:%M:%S}  {text}")
        while self.events.count() > 50:
            self.events.takeItem(self.events.count() - 1)

    def _on_session(self, info) -> None:
        self.session_info = info
        self.end_btn.setEnabled(bool(info))
        if info:
            self.banner.show_for(info)
            self.tray.setIcon(make_icon("#c62828"))
            kind = "Acesso NÃO SUPERVISIONADO" if info.get("unattended") else "Sessão remota iniciada"
            self.tray.showMessage(APP_NAME, f"{kind} por {info.get('operator')}",
                                  QSystemTrayIcon.Warning, 6000)
        else:
            self.banner.hide()
            self.tray.setIcon(make_icon())
        self._refresh_session_time()

    def _refresh_session_time(self) -> None:
        info = self.session_info
        if not info:
            self.session_label.setText("Nenhuma conexão ativa.")
            return
        since = datetime.fromtimestamp(info["since"])
        dur = int(time.time() - info["since"])
        route = "via TURN (retransmitida)" if info.get("relay") else "direta (P2P)"
        self.session_label.setText(
            f"<b>Operador conectado:</b> {_esc(info.get('operator'))}<br>"
            f"<b>Desde:</b> {since:%d/%m/%Y %H:%M:%S} ({dur // 60:02d}:{dur % 60:02d})<br>"
            f"<b>Permissão:</b> {PERM_LABEL.get(info.get('permission'), '-')}<br>"
            f"<b>Origem:</b> {_esc(info.get('ip'))} · conexão {route}")

    def _on_auth(self, rid: int, request: dict) -> None:
        self._show()
        dlg = AuthorizationDialog(request, int(self.settings.get("authorization_timeout_s") or 60), self)
        self._dialogs[rid] = dlg
        QApplication.beep()

        def done(result: int) -> None:
            self._dialogs.pop(rid, None)
            self.bridge.resolve(rid, result == QDialog.Accepted, dlg.permission())

        dlg.finished.connect(done)
        dlg.open()
        dlg.raise_()
        dlg.activateWindow()

    def _on_auth_cancel(self, rid: int) -> None:
        dlg = self._dialogs.pop(rid, None)
        if dlg:
            dlg.reject()

    # ---------------------------------------------------------------- saída
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(APP_NAME, "O HP2P continua em execução na bandeja.", QSystemTrayIcon.Information, 2500)

    def quit(self) -> None:
        if self.session_info and ask_yes_no(
                self, APP_NAME, "Há uma sessão remota ativa. Encerrar e sair?") is False:
            return
        self._quitting = True
        self.banner.hide()
        self.thread.stop()
        self.thread.join(timeout=5)
        self.tray.hide()
        QApplication.quit()


def ask_yes_no(parent, title: str, text: str) -> bool:
    """Pergunta Sim/Não em português (o executável não inclui as traduções do Qt)."""
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    yes = box.addButton("Sim", QMessageBox.YesRole)
    no = box.addButton("Não", QMessageBox.NoRole)
    box.setDefaultButton(no)
    box.exec()
    return box.clickedButton() is yes


def _esc(v) -> str:
    return (str(v if v is not None else "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def set_startup(enabled: bool) -> None:
    """Iniciar com o Windows (HKCU\\...\\Run), sem privilégios de administrador."""
    if sys.platform != "win32":
        return
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    if FROZEN:
        cmd = f'"{sys.executable}" --minimized'
    else:
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        cmd = f'"{pyw}" "{__import__("os").path.abspath(sys.argv[0])}" --minimized'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
