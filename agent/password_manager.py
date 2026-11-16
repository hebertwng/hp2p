"""Gerenciamento das senhas de acesso remoto.

A senha NUNCA é armazenada. O agente guarda apenas um verificador SRP-6a calculado sobre a saída
do Argon2id da senha:

    P' = Argon2id(senha, salt_kdf, m=64 MiB, t=2, p=1, 32 bytes)      (custo por tentativa offline)
    x  = SHA-256(salt_srp | SHA-256("hp2p:" | hex(P')))                (RFC 5054)
    v  = g^x mod N   (grupo RFC 5054 de 2048 bits)

Quem roubar o arquivo de credenciais (que ainda é protegido por DPAPI) obtém só (salts, v): para
testar cada senha candidata precisa pagar um Argon2id de 64 MiB. O navegador calcula P' com o mesmo
Argon2id (hash-wasm) e prova conhecer a senha pelo protocolo SRP-6a (ver authentication.py).

Tipos de senha:
  * automática  – gerada a cada início do aplicativo (visível/copiável enquanto o app roda);
  * personalizada – definida pelo usuário, persistida apenas como verificador (não pode ser exibida);
  * temporária  – adicional, com validade (ex.: 60 min para um atendimento de suporte).
Trocar uma senha invalida imediatamente a anterior (o verificador antigo é descartado e as
autenticações em andamento são canceladas).
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import string
import threading
import time
from dataclasses import dataclass
from typing import Callable

from argon2.low_level import Type, hash_secret_raw
from srp import _pysrp as srp  # backend fixo (o pacote pode trocar para OpenSSL conforme a máquina)

from config import base_dir
from identity import new_device_id
from security import dpapi_protect, dpapi_unprotect

log = logging.getLogger("hp2p.password")

srp.rfc5054_enable()  # fórmulas exatas do RFC 5054 (k, u e H(N) xor H(g) com padding)

SRP_USERNAME = "hp2p"
KDF_PARAMS = {"alg": "argon2id", "m": 65536, "t": 2, "p": 1, "len": 32}
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
MIN_CUSTOM_LENGTH = 10


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s)


def generate_password(length: int = 12) -> str:
    """Senha aleatória (secrets) com maiúsculas, minúsculas e números, sem caracteres ambíguos."""
    length = max(12, length)
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if any(c.isupper() for c in pw) and any(c.islower() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def validate_custom(password: str) -> str | None:
    """Retorna mensagem de erro ou None se a senha personalizada for aceitável."""
    if len(password) < MIN_CUSTOM_LENGTH:
        return f"A senha deve ter pelo menos {MIN_CUSTOM_LENGTH} caracteres."
    classes = sum([any(c.islower() for c in password), any(c.isupper() for c in password),
                   any(c.isdigit() for c in password), any(not c.isalnum() for c in password)])
    if classes < 3:
        return "Use pelo menos 3 tipos: minúsculas, maiúsculas, números, símbolos."
    return None


def kdf(password: str, salt: bytes, params: dict = KDF_PARAMS) -> str:
    raw = hash_secret_raw(password.encode("utf-8"), salt, time_cost=params["t"],
                          memory_cost=params["m"], parallelism=params["p"],
                          hash_len=params["len"], type=Type.ID)
    return raw.hex()


def make_verifier(password: str) -> dict:
    kdf_salt = secrets.token_bytes(16)
    p_prime = kdf(password, kdf_salt)
    s, v = srp.create_salted_verification_key(SRP_USERNAME, p_prime, hash_alg=srp.SHA256,
                                              ng_type=srp.NG_2048, salt_len=16)
    return {"kdf": {**KDF_PARAMS, "salt": b64(kdf_salt)}, "srp": {"salt": b64(s), "v": b64(v)}}


@dataclass
class Credential:
    kind: str                 # "auto" | "custom" | "temp"
    verifier: dict
    expires_at: float | None = None  # epoch (time.time) para temporárias

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at


class PasswordManager:
    def __init__(self, auto_password: bool = True):
        self._lock = threading.RLock()
        self._file = base_dir() / "credentials.bin"
        self._data = self._load()
        self.epoch = 0                 # muda a cada troca de senha -> invalida handshakes em curso
        self.listeners: list[Callable[[], None]] = []
        self._auto_plain: str | None = None
        self._auto: Credential | None = None
        if not self._data.get("device_id"):
            self._data["device_id"] = new_device_id()
            self._save()
        if auto_password or not self._data.get("custom"):
            self.regenerate_auto()

    # ------------------------------------------------------------ armazenamento
    def _load(self) -> dict:
        if not self._file.exists():
            return {}
        try:
            return json.loads(dpapi_unprotect(self._file.read_bytes()).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("Não foi possível ler as credenciais protegidas (%s). Criando novas.", exc)
            return {}

    def _save(self) -> None:
        tmp = self._file.with_suffix(".tmp")
        tmp.write_bytes(dpapi_protect(json.dumps(self._data).encode("utf-8")))
        tmp.replace(self._file)

    def _changed(self) -> None:
        self.epoch += 1
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("listener de troca de senha falhou")

    # -------------------------------------------------------------------- ID
    @property
    def device_id(self) -> str:
        return self._data["device_id"]

    def regenerate_device_id(self) -> str:
        with self._lock:
            self._data["device_id"] = new_device_id()
            self._save()
        log.info("Novo ID gerado")
        return self.device_id

    # ----------------------------------------------------------------- senhas
    @property
    def mode(self) -> str:
        return "custom" if (self._auto is None and self._data.get("custom")) else "auto"

    @property
    def visible_password(self) -> str | None:
        """Texto da senha automática (só existe em memória). None para senha personalizada."""
        return self._auto_plain if self.mode == "auto" else None

    def regenerate_auto(self) -> str:
        with self._lock:
            pw = generate_password(12)
            self._auto_plain = pw
            self._auto = Credential("auto", make_verifier(pw))
        log.info("Nova senha automática gerada (senha anterior invalidada)")
        self._changed()
        return pw

    def set_custom(self, password: str) -> None:
        err = validate_custom(password)
        if err:
            raise ValueError(err)
        with self._lock:
            self._data["custom"] = make_verifier(password)
            self._save()
            self._auto = None
            self._auto_plain = None
        log.info("Senha personalizada definida (senha anterior invalidada)")
        self._changed()

    def use_auto(self) -> str:
        with self._lock:
            self._data.pop("custom", None)
            self._save()
        return self.regenerate_auto()

    def set_temporary(self, password: str, minutes: int) -> None:
        err = validate_custom(password) if len(password) < 12 else None
        if err:
            raise ValueError(err)
        with self._lock:
            self._data["temp"] = {"verifier": make_verifier(password),
                                  "expires_at": time.time() + minutes * 60}
            self._save()
        log.info("Senha temporária definida por %d min", minutes)
        self._changed()

    def clear_temporary(self) -> None:
        with self._lock:
            if self._data.pop("temp", None) is not None:
                self._save()
                log.info("Senha temporária removida")
                self._changed()

    def temporary_expires_at(self) -> float | None:
        t = self._data.get("temp")
        if t and time.time() < t["expires_at"]:
            return t["expires_at"]
        return None

    def active_credentials(self) -> list[Credential]:
        with self._lock:
            out: list[Credential] = []
            if self._auto is not None:
                out.append(self._auto)
            elif self._data.get("custom"):
                out.append(Credential("custom", self._data["custom"]))
            t = self._data.get("temp")
            if t:
                c = Credential("temp", t["verifier"], t["expires_at"])
                if c.expired:
                    self._data.pop("temp", None)
                    self._save()
                else:
                    out.append(c)
            return out
