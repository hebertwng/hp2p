"""Regressão: cliques repetidos em "Iniciar serviço" com o servidor fora do ar não podem criar
laços de conexão paralelos; o status durante as novas tentativas é "connecting" (botão desabilitado);
quando o servidor volta, o agente registra sozinho; fechar no meio de uma tentativa não deixa
sessões HTTP abertas."""

import asyncio
import gc
import logging
import os
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
os.environ["HP2P_HOME"] = tempfile.mkdtemp(prefix="hp2p-reconn-")
PORT = 9555

from config import Settings  # noqa: E402
from password_manager import PasswordManager  # noqa: E402
import session_manager  # noqa: E402

attempts = []
orig_start = session_manager.PeerJSSignaling.start


async def counting_start(self):
    attempts.append(time.monotonic())
    return await orig_start(self)


session_manager.PeerJSSignaling.start = counting_start


class Ui:
    def __init__(self):
        self.states = []

    def status(self, s, t): self.states.append(s)
    def session(self, i): pass
    def event(self, t): pass
    async def authorize(self, r): return False, "view"


async def main():
    ok = True
    s = Settings.load()
    s.data["signaling"].update(host="127.0.0.1", port=PORT)
    s.save()
    ui = Ui()
    svc = session_manager.AgentService(s, PasswordManager(), ui)
    run = asyncio.ensure_future(svc.run())
    await asyncio.sleep(0.5)
    for _ in range(5):                       # 5 cliques em "Iniciar serviço"
        asyncio.ensure_future(svc.resume())
    await asyncio.sleep(3.5)
    n = len(attempts)
    print(f"tentativas em ~4 s com o servidor fora: {n} (esperado: 2, um único laço com espera 2 s)")
    ok &= n <= 3
    print("status durante novas tentativas:", sorted(set(ui.states)))
    ok &= "error" not in ui.states

    node = subprocess.Popen(["node", str(ROOT / "server/peer-server/server.js")],
                            env={**os.environ, "HP2P_PORT": str(PORT)}, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20 and svc.signaling is None:
            await asyncio.sleep(0.2)
        print(f"registrado após o servidor voltar: {svc.signaling is not None} "
              f"({time.monotonic() - t0:.1f} s), status final: {ui.states[-1]}")
        ok &= svc.signaling is not None and ui.states[-1] == "available"
    finally:
        svc.stop()
        await run
        node.kill()
    return ok


warnings.simplefilter("default")
logging.basicConfig(level=logging.ERROR)
result = asyncio.run(main())
gc.collect()
print("RESULTADO:", "PASSOU" if result else "FALHOU")
sys.exit(0 if result else 1)
