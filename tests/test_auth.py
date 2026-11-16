"""Testes de autenticação: interoperabilidade SRP-6a navegador(JS) <-> agente(Python) e cenários de ataque.

Executa o authentication.js real do controlador web no Node e o authentication.py real do agente.
Uso:  agent\\.venv\\Scripts\\python tests\\test_auth.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
os.environ["HP2P_HOME"] = tempfile.mkdtemp(prefix="hp2p-test-")

from authentication import AuthError, AuthHandshake  # noqa: E402
from password_manager import PasswordManager  # noqa: E402
from security import AttemptLimiter  # noqa: E402

FP_AGENT = "AA:" * 31 + "AA"
FP_CTRL = "BB:" * 31 + "BB"
RESULTS: list[tuple[str, bool, str]] = []


class Node:
    def __init__(self):
        self.p = subprocess.Popen(["node", str(ROOT / "tests" / "srp_client_node.js")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8")

    def call(self, **cmd):
        self.p.stdin.write(json.dumps(cmd) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.call(op="quit") if False else None
        self.p.kill()


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    print(f"[{'PASSOU' if cond else 'FALHOU'}] {name}{' — ' + detail if detail else ''}")


def attempt(node: Node, pm: PasswordManager, limiter: AttemptLimiter, password: str,
            fp_seen_by_ctrl_agent: str = FP_AGENT, fp_seen_by_agent_ctrl: str = FP_CTRL):
    """Executa um handshake completo. Retorna (ok_msg|None, erro|None, tempo_ms_do_navegador)."""
    A = node.call(op="hello")["A"]
    hs = AuthHandshake(pm, limiter, FP_AGENT, fp_seen_by_agent_ctrl)
    try:
        ch = hs.challenge({"A": A})
    except AuthError as e:
        return None, e, 0, hs, None
    r = node.call(op="prove", challenge=ch, password=password, fpAgent=fp_seen_by_ctrl_agent, fpCtrl=FP_CTRL)
    if "error" in r:
        raise RuntimeError(r["error"])
    try:
        ok = hs.verify(r["proof"])
    except AuthError as e:
        return None, e, r["ms"], hs, r["proof"]
    return ok, None, r["ms"], hs, r["proof"]


def main() -> int:
    node = Node()
    pm = PasswordManager(auto_password=True)
    limiter = AttemptLimiter(max_failures=3, window_s=300, lockout_s=2)
    pw = pm.visible_password

    # 1. senha correta + verificação mútua (HAMK + vínculo DTLS)
    ok, err, ms, hs, _ = attempt(node, pm, limiter, pw)
    check("Senha correta aceita (JS -> pysrp)", ok is not None, f"tempo no navegador (Argon2id+SRP): {ms} ms")
    v = node.call(op="verify", ok=ok, sid=hs.sid, fpAgent=FP_AGENT, fpCtrl=FP_CTRL)
    check("Navegador confirma identidade do agente (HAMK + bind)", v.get("agentVerified") is True)

    # 2. agente falso (não conhece o verificador) não consegue se passar pelo verdadeiro
    fake = dict(ok, HAMK=ok["HAMK"][:-4] + "AAA=")
    v = node.call(op="verify", ok=fake, sid=hs.sid, fpAgent=FP_AGENT, fpCtrl=FP_CTRL)
    check("Agente falso (HAMK inválido) é rejeitado pelo navegador", v.get("agentVerified") is False)

    # 3. senha errada
    ok, err, *_ = attempt(node, pm, limiter, "SenhaErrada123")
    check("Senha incorreta rejeitada", ok is None and err.reason == "wrong-password",
          f"tentativas restantes: {err.extra.get('remaining') if err else '?'}")

    # 4. replay: reenviar a mesma prova no mesmo desafio
    limiter.register_success()
    ok, err, _, hs, proof = attempt(node, pm, limiter, pw)
    try:
        hs.verify(proof)
        replay_blocked = False
    except AuthError as e:
        replay_blocked = e.reason == "replay"
    check("Replay da prova no mesmo desafio é bloqueado", replay_blocked)

    # 5. replay em um NOVO desafio (B diferente)
    A = node.call(op="hello")["A"]
    hs2 = AuthHandshake(pm, limiter, FP_AGENT, FP_CTRL)
    ch2 = hs2.challenge({"A": A})
    try:
        hs2.verify({**proof, "sid": ch2["sid"]})
        replay2 = False
    except AuthError as e:
        replay2 = e.reason == "wrong-password"
    check("Prova antiga reaproveitada em novo desafio é rejeitada", replay2)

    # 6. MITM na sinalização: fingerprints diferentes em cada lado
    limiter.register_success()
    ok, err, *_ = attempt(node, pm, limiter, pw, fp_seen_by_ctrl_agent="CC:" * 31 + "CC")
    check("MITM (fingerprint DTLS trocada) detectado mesmo com senha correta",
          ok is None and err is not None and err.reason == "channel-binding")

    # 7. bloqueio após falhas consecutivas
    limiter.register_success()
    for _ in range(3):
        attempt(node, pm, limiter, "errada-errada")
    ok, err, *_ = attempt(node, pm, limiter, pw)
    check("Bloqueio temporário após 3 falhas (mesmo com a senha certa)", ok is None and err.reason == "locked",
          f"bloqueado por {err.extra.get('locked_for') if err else '?'} s")
    time.sleep(2.2)
    ok, err, *_ = attempt(node, pm, limiter, pw)
    check("Acesso volta após o fim do bloqueio", ok is not None)

    # 8. troca de senha invalida a anterior imediatamente
    old = pw
    new = pm.regenerate_auto()
    ok, err, *_ = attempt(node, pm, limiter, old)
    check("Senha anterior invalidada após gerar nova", ok is None)
    ok, err, *_ = attempt(node, pm, limiter, new)
    check("Nova senha funciona", ok is not None)

    # 9. troca de senha DURANTE a autenticação
    A = node.call(op="hello")["A"]
    hs3 = AuthHandshake(pm, limiter, FP_AGENT, FP_CTRL)
    ch3 = hs3.challenge({"A": A})
    r = node.call(op="prove", challenge=ch3, password=new, fpAgent=FP_AGENT, fpCtrl=FP_CTRL)
    pm.regenerate_auto()
    try:
        hs3.verify(r["proof"])
        changed = False
    except AuthError as e:
        changed = e.reason == "password-changed"
    check("Autenticação em curso cancelada quando a senha muda", changed)

    # 10. senha personalizada + temporária (duas credenciais ativas)
    pm.set_custom("Fixa-Senha-2026")
    pm.set_temporary("Temporaria-Suporte9", 30)
    ok1, *_ = attempt(node, pm, limiter, "Fixa-Senha-2026")
    ok2, *_ = attempt(node, pm, limiter, "Temporaria-Suporte9")
    check("Senha personalizada e senha temporária aceitas", ok1 is not None and ok2 is not None)
    pm.clear_temporary()
    ok3, *_ = attempt(node, pm, limiter, "Temporaria-Suporte9")
    check("Senha temporária removida deixa de funcionar", ok3 is None)

    # 11. armazenamento: nenhuma senha em texto no disco
    raw = (Path(os.environ["HP2P_HOME"]) / "credentials.bin").read_bytes()
    leaked = any(s.encode() in raw for s in ("Fixa-Senha-2026", "Temporaria-Suporte9", new))
    check("credentials.bin não contém senhas (DPAPI + só verificadores)", not leaked and not raw.startswith(b"{"))

    node.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nRESULTADO: {len(RESULTS) - len(failed)}/{len(RESULTS)} aprovados")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
