"""Autenticação do operador: SRP-6a (RFC 5054) + vínculo ao canal DTLS.

Por que SRP-6a: é um PAKE aumentado padronizado (RFC 2945/5054) e amplamente analisado. O agente
guarda só o verificador (não a senha nem um equivalente dela), a senha nunca trafega, cada sessão usa
efêmeros novos (sem replay) e a mensagem HAMK prova ao navegador que o agente conhece o verificador
(o agente não pode ser personificado por quem não tem o verificador).
A implementação do lado do agente é a biblioteca `srp` (pysrp), no modo RFC 5054.

Vínculo ao canal (channel binding): após o SRP, os dois lados trocam
    HMAC-SHA256(K, "HP2P-bind-v1|<papel>|<sid>|<fingerprint DTLS do agente>|<fingerprint DTLS do navegador>")
Cada lado usa as fingerprints que VÊ no seu próprio SDP. Se o servidor de sinalização (ou alguém no
caminho) trocou os SDPs para ficar no meio (MITM), as fingerprints vistas diferem e o HMAC falha.

Mensagens (JSON no DataChannel):
  C->A hello     {t, v, name, A}
  A->C challenge {t, sid, user, group, hash, creds:[{kdf:{alg,m,t,p,len,salt}, srp:{salt,B}}]}
  C->A proof     {t, sid, proofs:[{M, bind}]}            (uma prova por credencial ativa)
  A->C auth-ok   {t, HAMK, bind}  |  auth-fail {t, reason, remaining, locked_for}
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass

from srp import _pysrp as srp

from password_manager import SRP_USERNAME, Credential, PasswordManager
from security import AttemptLimiter, consteq

log = logging.getLogger("hp2p.auth")

srp.rfc5054_enable()
HANDSHAKE_TIMEOUT_S = 90
BIND_LABEL = "HP2P-bind-v1"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str, max_len: int = 1024) -> bytes:
    if not isinstance(s, str) or len(s) > max_len * 2:
        raise ValueError("campo inválido")
    return base64.b64decode(s, validate=True)


_FP_STRENGTH = {"sha-512": 3, "sha-384": 2, "sha-256": 1}


def sdp_fingerprint(sdp: str) -> str:
    """Fingerprint DTLS mais forte presente no SDP, como "sha-512 AB:CD:…".

    O aiortc anuncia sha-256, sha-384 e sha-512; o Chrome guarda apenas a mais forte na
    remoteDescription. Usar sempre a mais forte disponível dá o mesmo valor nos dois lados.
    """
    found = re.findall(r"^a=fingerprint:(sha-256|sha-384|sha-512) ([0-9A-Fa-f:]+)\s*$", sdp or "", re.MULTILINE)
    if not found:
        raise ValueError("SDP sem fingerprint DTLS")
    alg, value = max(found, key=lambda f: _FP_STRENGTH[f[0].lower()])
    return f"{alg.lower()} {value.upper()}"


def bind_mac(key: bytes, role: str, sid: str, fp_agent: str, fp_ctrl: str) -> bytes:
    msg = f"{BIND_LABEL}|{role}|{sid}|{fp_agent}|{fp_ctrl}".encode()
    return hmac.new(key, msg, hashlib.sha256).digest()


class AuthError(Exception):
    def __init__(self, reason: str, **extra):
        super().__init__(reason)
        self.reason = reason
        self.extra = extra


@dataclass
class _Pending:
    cred: Credential
    verifier: object


class AuthHandshake:
    """Estado de UMA tentativa de autenticação (um sid, uso único)."""

    def __init__(self, pm: PasswordManager, limiter: AttemptLimiter, fp_agent: str, fp_ctrl: str):
        self.pm = pm
        self.limiter = limiter
        self.fp_agent = fp_agent
        self.fp_ctrl = fp_ctrl
        self.sid = b64(secrets.token_bytes(16))
        self.created = time.monotonic()
        self.epoch = pm.epoch
        self._pending: list[_Pending] = []
        self.used = False
        self.session_key: bytes | None = None
        self.credential_kind: str | None = None

    def challenge(self, hello: dict) -> dict:
        locked = self.limiter.locked_for()
        if locked > 0:
            raise AuthError("locked", locked_for=int(locked) + 1)
        A = unb64(hello.get("A", ""), 512)
        if len(A) < 32:
            raise AuthError("bad-request")
        creds = self.pm.active_credentials()
        if not creds:
            raise AuthError("no-password")
        out = []
        for c in creds:
            s = base64.b64decode(c.verifier["srp"]["salt"])
            v = base64.b64decode(c.verifier["srp"]["v"])
            ver = srp.Verifier(SRP_USERNAME, s, v, A, hash_alg=srp.SHA256, ng_type=srp.NG_2048)
            s_out, B = ver.get_challenge()
            if s_out is None or B is None:  # verificação de segurança do SRP-6a (A mod N == 0)
                raise AuthError("bad-request")
            self._pending.append(_Pending(c, ver))
            out.append({"kdf": c.verifier["kdf"], "srp": {"salt": b64(s_out), "B": b64(B)}})
        return {"t": "challenge", "sid": self.sid, "user": SRP_USERNAME, "group": "rfc5054-2048",
                "hash": "SHA-256", "creds": out}

    def verify(self, proof: dict) -> dict:
        """Valida a prova. Retorna a mensagem auth-ok ou lança AuthError (já contabilizando a falha)."""
        if self.used:
            raise AuthError("replay")
        self.used = True  # desafio descartado após o uso, com ou sem sucesso
        if proof.get("sid") != self.sid:
            raise AuthError("replay")
        if time.monotonic() - self.created > HANDSHAKE_TIMEOUT_S:
            raise AuthError("timeout")
        if self.pm.epoch != self.epoch:
            raise AuthError("password-changed")
        proofs = proof.get("proofs")
        if not isinstance(proofs, list) or len(proofs) != len(self._pending):
            raise AuthError("bad-request")

        for pend, p in zip(self._pending, proofs):
            try:
                M = unb64(p.get("M", ""), 64)
                cbind = unb64(p.get("bind", ""), 64)
            except (ValueError, AttributeError):
                raise AuthError("bad-request") from None
            hamk = pend.verifier.verify_session(M)
            if hamk is None:
                continue
            if pend.cred.expired:
                continue
            K = pend.verifier.get_session_key()
            expected = bind_mac(K, "controller", self.sid, self.fp_agent, self.fp_ctrl)
            if not consteq(expected, cbind):
                # senha correta, mas o canal DTLS não é o mesmo dos dois lados -> possível MITM
                log.warning("Senha correta porém vínculo DTLS inválido: possível interceptação. Recusado.")
                raise AuthError("channel-binding")
            self.limiter.register_success()
            self.session_key = K
            self.credential_kind = pend.cred.kind
            return {"t": "auth-ok", "HAMK": b64(hamk),
                    "bind": b64(bind_mac(K, "agent", self.sid, self.fp_agent, self.fp_ctrl))}

        lock = self.limiter.register_failure()
        raise AuthError("wrong-password", remaining=self.limiter.remaining_attempts(),
                        locked_for=int(lock))
