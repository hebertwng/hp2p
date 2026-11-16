"""HP2P - agente de acesso remoto (ponto de entrada).

    python main.py               abre a interface gráfica
    python main.py --minimized   inicia na bandeja (usado por "Iniciar com o Windows")
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

from config import APP_NAME, FROZEN, Settings, base_dir


def setup_logging(debug: bool) -> None:
    logdir = base_dir() / "logs"
    logdir.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        logdir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if not FROZEN and sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("aioice", "aiortc", "aiohttp", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--minimized", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    setup_logging(args.debug)
    log = logging.getLogger("hp2p")

    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from interface import MainWindow, QtBridge, ServiceThread, make_icon
    from password_manager import PasswordManager
    from session_manager import AgentService

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())

    lock = QLockFile(str(base_dir() / "agent.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "O HP2P já está em execução (veja o ícone na bandeja).")
        return 0

    settings = Settings.load()
    pm = PasswordManager(auto_password=bool(settings.get("auto_password")))
    bridge = QtBridge()
    service = AgentService(settings, pm, bridge)
    thread = ServiceThread(service, bridge)
    thread.start()
    thread.ready.wait(5)

    win = MainWindow(settings, pm, bridge, thread)
    if not (args.minimized or settings.get("start_minimized")):
        win.show()
    log.info("%s iniciado (ID %s)", APP_NAME, pm.device_id)
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
