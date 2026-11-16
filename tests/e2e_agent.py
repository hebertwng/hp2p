"""Agente HP2P real, sem interface gráfica, para testes ponta a ponta automatizados.

Usa o AgentService/sinalização/WebRTC/captura/SendInput reais. Só substitui a janela de autorização
por uma decisão automática (HP2P_TEST_DECISION = accept-full | accept-mouse | accept-view | reject).
NÃO faz parte do executável distribuído.

    agent\\.venv\\Scripts\\python tests\\e2e_agent.py --port 9443 --password Teste-HP2P-2026
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))


class HeadlessUi:
    def __init__(self, decision: str, state_file: Path):
        self.decision = decision
        self.state_file = state_file
        self.state: dict = {"status": None, "session": None, "events": [], "auth_requests": []}

    def _dump(self):
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def status(self, state, text):
        print(f"[status] {state}: {text}", flush=True)
        self.state["status"] = [state, text]
        self._dump()

    def session(self, info):
        print(f"[session] {info}", flush=True)
        self.state["session"] = info
        self._dump()

    def event(self, text):
        print(f"[event] {text}", flush=True)
        self.state["events"].append(text)
        self._dump()

    async def authorize(self, request):
        print(f"[authorize] {request} -> {self.decision}", flush=True)
        self.state["auth_requests"].append(request)
        self._dump()
        await asyncio.sleep(0.5)
        if self.decision == "reject":
            return False, "view"
        return True, self.decision.split("-", 1)[1]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9443)
    ap.add_argument("--password", default="Teste-HP2P-2026")
    ap.add_argument("--home", default=None)
    ap.add_argument("--local", action="store_true", help="usa o PeerServer local em vez do PeerJS Cloud")
    a = ap.parse_args()
    home = Path(a.home or tempfile.mkdtemp(prefix="hp2p-e2e-"))
    os.environ["HP2P_HOME"] = str(home)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", stream=sys.stdout)
    for n in ("aioice", "aiortc", "aiohttp"):
        logging.getLogger(n).setLevel(logging.WARNING)

    from config import Settings
    from identity import display
    from password_manager import PasswordManager
    from session_manager import AgentService

    settings = Settings.load()
    if a.local:  # PeerServer próprio local; sem --local usa o padrão (PeerJS Cloud)
        settings.data["signaling"].update({"host": "localhost", "port": a.port, "path": "/signal",
                                           "key": "hp2p", "secure": False})
        settings.save()
    print("sinalização:", settings.get("signaling"), flush=True)
    pm = PasswordManager(auto_password=False)
    pm.set_custom(a.password)
    ui = HeadlessUi(os.environ.get("HP2P_TEST_DECISION", "accept-full"), home / "state.json")
    ui.state["device_id"] = pm.device_id
    print(f"HP2P_DEVICE_ID={pm.device_id} ({display(pm.device_id)})", flush=True)
    svc = AgentService(settings, pm, ui)

    # Comandos de controle do teste via arquivo (encerrar sessão, trocar decisão)
    async def control():
        ctl = home / "control.json"
        while True:
            await asyncio.sleep(0.3)
            if ctl.exists():
                cmd = json.loads(ctl.read_text(encoding="utf-8"))
                ctl.unlink()
                if cmd.get("decision"):
                    ui.decision = cmd["decision"]
                if cmd.get("end_session"):
                    await svc.end_session("encerrada pelo usuário local")
                if cmd.get("quit"):
                    svc.stop()
                    return

    asyncio.ensure_future(control())
    await svc.run()


if __name__ == "__main__":
    asyncio.run(main())
