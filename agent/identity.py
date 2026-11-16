"""Identificação pública do computador.

O ID é um identificador PÚBLICO (não é credencial): 9 dígitos aleatórios gerados com `secrets`,
exibidos como 583-921-764. No PeerServer o peer é registrado como "hp2p-583921764" para separar
o espaço de nomes dos agentes do espaço dos controladores ("op-…").
"""

from __future__ import annotations

import re
import secrets

PEER_PREFIX = "hp2p-"


def new_device_id() -> str:
    # 9 dígitos, primeiro dígito != 0 (900 milhões de combinações)
    return str(secrets.randbelow(900_000_000) + 100_000_000)


def normalize(user_input: str) -> str | None:
    digits = re.sub(r"\D", "", user_input or "")
    return digits if len(digits) == 9 else None


def display(device_id: str) -> str:
    return f"{device_id[0:3]}-{device_id[3:6]}-{device_id[6:9]}"


def peer_id(device_id: str) -> str:
    return PEER_PREFIX + device_id
