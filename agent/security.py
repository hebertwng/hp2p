"""Primitivas de segurança do agente: DPAPI, limitador de tentativas, limitador de taxa e validação.

Nada aqui inventa criptografia: DPAPI é a API de proteção de dados do Windows (CryptProtectData),
os aleatórios vêm do módulo secrets e as comparações usam hmac.compare_digest.
"""

from __future__ import annotations

import ctypes
import hmac
import logging
import sys
import threading
import time
from ctypes import wintypes

log = logging.getLogger("hp2p.security")

_ENTROPY = b"HP2P-agent-credentials-v1"


# ------------------------------------------------------------------------- DPAPI
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_protect(data: bytes) -> bytes:
    """Criptografa com a chave do usuário atual do Windows (CryptProtectData)."""
    if sys.platform != "win32":
        raise RuntimeError("DPAPI disponível apenas no Windows")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    inp, ent, out = _blob(data), _blob(_ENTROPY), _DataBlob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not crypt32.CryptProtectData(ctypes.byref(inp), "HP2P", ctypes.byref(ent), None, None,
                                    CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("DPAPI disponível apenas no Windows")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    inp, ent, out = _blob(data), _blob(_ENTROPY), _DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(inp), None, ctypes.byref(ent), None, None,
                                      0x1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ------------------------------------------------------------ limite de tentativas
class AttemptLimiter:
    """Bloqueio temporário após falhas consecutivas de autenticação.

    Conta falhas numa janela deslizante. Ao atingir `max_failures`, bloqueia novas tentativas
    por `lockout_s` segundos (o tempo dobra a cada novo bloqueio, até 1 h).
    """

    def __init__(self, max_failures: int = 5, window_s: float = 300, lockout_s: float = 300):
        self.max_failures = max_failures
        self.window_s = window_s
        self.base_lockout = lockout_s
        self._failures: list[float] = []
        self._locked_until = 0.0
        self._lockouts = 0
        self._lock = threading.Lock()

    def locked_for(self) -> float:
        with self._lock:
            return max(0.0, self._locked_until - time.monotonic())

    def register_failure(self) -> float:
        """Registra uma falha; retorna os segundos de bloqueio aplicados (0 se nenhum)."""
        now = time.monotonic()
        with self._lock:
            self._failures = [t for t in self._failures if now - t < self.window_s] + [now]
            if len(self._failures) >= self.max_failures:
                self._lockouts += 1
                dur = min(self.base_lockout * (2 ** (self._lockouts - 1)), 3600)
                self._locked_until = now + dur
                self._failures.clear()
                return dur
            return 0.0

    def register_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._lockouts = 0

    def remaining_attempts(self) -> int:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures if now - t < self.window_s]
            return max(0, self.max_failures - len(recent))


class TokenBucket:
    """Limitador de taxa para eventos de entrada (mouse/teclado)."""

    def __init__(self, rate_per_s: float, burst: float | None = None):
        self.rate = rate_per_s
        self.capacity = burst if burst is not None else rate_per_s
        self.tokens = self.capacity
        self.last = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


def consteq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def safe_text(value: object, max_len: int = 64) -> str:
    """Texto vindo do controlador para exibir na UI/log: sem controles, tamanho limitado."""
    s = str(value or "")
    s = "".join(ch for ch in s if ch.isprintable())
    return s[:max_len].strip() or "-"
