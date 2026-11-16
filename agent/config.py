"""Configuração externa do agente HP2P.

Arquivos (por usuário do Windows):
  %APPDATA%\\HP2P\\settings.json     configurações não sensíveis (editáveis)
  %APPDATA%\\HP2P\\credentials.bin   ID + verificadores de senha, protegidos por DPAPI
  %APPDATA%\\HP2P\\logs\\agent.log    log técnico rotativo (sem senhas)

A variável HP2P_HOME troca a pasta base (usada em testes).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("hp2p.config")

APP_NAME = "HP2P"
APP_VERSION = "1.0.0"
PROTOCOL_VERSION = 1
FROZEN = getattr(sys, "frozen", False)
CLOUD_HOST = "0.peerjs.com"


def base_dir() -> Path:
    custom = os.environ.get("HP2P_HOME")
    if custom:
        p = Path(custom)
    else:
        p = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS: dict[str, Any] = {
    # Sinalização: PeerJS Cloud público (mesmos valores padrão da biblioteca PeerJS).
    # Para um PeerServer próprio (server/peer-server): host/porta do servidor, path "/signal", key "hp2p".
    "signaling": {
        "host": CLOUD_HOST,
        "port": 443,
        "path": "/",
        "key": "peerjs",
        "secure": True,
    },
    # Endpoint de iceServers com TURN temporário (só existe no PeerServer próprio, GET /hp2p/ice).
    # Vazio = automático (usado apenas quando a sinalização NÃO é o PeerJS Cloud).
    "ice_endpoint": "",
    # STUN do Google + TURN públicos do projeto PeerJS (os mesmos que a biblioteca PeerJS usa por padrão)
    "ice_servers": [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": ["turn:eu-0.turn.peerjs.com:3478", "turn:us-0.turn.peerjs.com:3478"],
         "username": "peerjs", "credential": "peerjsp"},
    ],
    "auto_password": True,          # gera uma senha nova a cada início do aplicativo
    "start_with_windows": False,
    "start_minimized": False,
    "authorization_timeout_s": 60,
    "video": {
        "monitor": 1,               # 1 = monitor principal (índice do mss)
        "quality": "auto",          # auto | low | medium | high
        "max_bitrate": 4_000_000,
    },
    "security": {
        "max_failures": 5,          # falhas de senha antes do bloqueio
        "failure_window_s": 300,
        "lockout_s": 300,
        "max_input_events_per_s": 400,
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Settings:
    data: dict

    @property
    def path(self) -> Path:
        return base_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = base_dir() / "settings.json"
        data: dict = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                log.error("settings.json inválido (%s); usando padrões", exc)
        # Ao lado do .exe pode existir um settings.json de fábrica (distribuição pré-configurada).
        # O instalador grava um "factory_id" novo a cada instalação: nesse caso o servidor escolhido no
        # instalador substitui o da configuração antiga do usuário (reinstalação com outro servidor).
        factory = Path(sys.executable if FROZEN else __file__).with_name("settings.json")
        changed = not p.exists()
        if factory.exists():
            try:
                fdata = json.loads(factory.read_text(encoding="utf-8-sig"))
                fid = fdata.get("factory_id")
                if not p.exists() or (fid and fid != data.get("factory_id")):
                    data = _merge(data, fdata)
                    changed = True
                    log.info("Configuração de fábrica aplicada: %s", factory)
            except (OSError, json.JSONDecodeError):
                pass
        s = cls(_merge(DEFAULTS, data))
        if changed:
            s.save()
        return s

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def set(self, value: Any, *keys: str) -> None:
        cur = self.data
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value
        self.save()

    def ice_endpoint_url(self) -> str | None:
        if self.get("ice_endpoint"):
            return self.get("ice_endpoint")
        sig = self.get("signaling")
        if sig.get("host") == CLOUD_HOST:
            return None  # PeerJS Cloud não tem /hp2p/ice: usa ice_servers

        scheme = "https" if sig.get("secure") else "http"
        return f"{scheme}://{sig['host']}:{sig['port']}/hp2p/ice"
