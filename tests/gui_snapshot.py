"""Gera capturas (PNG) das telas do agente usando a interface REAL, com uma pasta de dados temporária.

    agent\\.venv\\Scripts\\python tests\\gui_snapshot.py docs\\img
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
os.environ["HP2P_HOME"] = tempfile.mkdtemp(prefix="hp2p-gui-")
out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "img")
out.mkdir(parents=True, exist_ok=True)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from config import Settings  # noqa: E402
from interface import AuthorizationDialog, MainWindow, QtBridge, ServiceThread, SessionBanner, SettingsDialog  # noqa: E402
from password_manager import PasswordManager  # noqa: E402
from session_manager import AgentService  # noqa: E402

app = QApplication(sys.argv)
settings = Settings.load()
settings.data["signaling"]["port"] = int(os.environ.get("HP2P_PORT", "9443"))
settings.save()
pm = PasswordManager(auto_password=True)
bridge = QtBridge()
svc = AgentService(settings, pm, bridge)
th = ServiceThread(svc, bridge)
th.start()
th.ready.wait(5)
win = MainWindow(settings, pm, bridge, th)
win.show()


def shots():
    win.grab().save(str(out / "agente-principal.png"))
    req = {"operator": "Operador 01", "peer": "op-3f9c2a1b7d4e6f80", "ip": "192.168.0.10", "relay": False, "credential": "auto"}
    dlg = AuthorizationDialog(req, 60)
    dlg.show()
    QApplication.processEvents()
    dlg.grab().save(str(out / "agente-autorizacao.png"))
    dlg.close()
    win._on_session({"operator": "Operador 01", "peer": "op-3f9c", "since": time.time() - 95,
                     "permission": "full", "ip": "192.168.0.10", "relay": False})
    QApplication.processEvents()
    win.grab().save(str(out / "agente-sessao-ativa.png"))
    win.banner.grab().save(str(out / "agente-indicador-sessao.png"))
    win._on_session(None)
    sd = SettingsDialog(settings, pm)
    sd.show()
    QApplication.processEvents()
    sd.grab().save(str(out / "agente-configuracoes.png"))
    sd.close()
    print("capturas salvas em", out)
    win._quitting = True
    th.stop()
    th.join(5)
    win.tray.hide()
    app.quit()


QTimer.singleShot(4000, shots)
app.exec()
