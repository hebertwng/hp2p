"""Controle de mouse via SendInput (coordenadas absolutas no desktop virtual).

O controlador envia coordenadas normalizadas (0..1) relativas à imagem do monitor transmitido; aqui
elas são convertidas para o retângulo real do monitor dentro do desktop virtual (multi-monitor).
"""

from __future__ import annotations

import logging
import math

import win_input as wi
from screen_capture import Monitor

log = logging.getLogger("hp2p.mouse")

MOVE, LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP = 0x1, 0x2, 0x4, 0x8, 0x10
MIDDLEDOWN, MIDDLEUP, XDOWN, XUP = 0x20, 0x40, 0x80, 0x100
WHEEL, HWHEEL, VIRTUALDESK, ABSOLUTE = 0x800, 0x1000, 0x4000, 0x8000
WHEEL_DELTA = 120

# botão (MouseEvent.button do navegador) -> (flag down, flag up, mouseData)
_BUTTONS = {0: (LEFTDOWN, LEFTUP, 0), 1: (MIDDLEDOWN, MIDDLEUP, 0), 2: (RIGHTDOWN, RIGHTUP, 0),
            3: (XDOWN, XUP, 1), 4: (XDOWN, XUP, 2)}


class MouseController:
    def __init__(self, monitor: Monitor, virtual: Monitor):
        self.monitor = monitor
        self.virtual = virtual
        self.pressed: set[int] = set()

    def _abs(self, nx: float, ny: float) -> tuple[int, int]:
        if not (math.isfinite(nx) and math.isfinite(ny)):
            raise ValueError("coordenada inválida")
        nx = min(1.0, max(0.0, nx))
        ny = min(1.0, max(0.0, ny))
        px = self.monitor.left + nx * (self.monitor.width - 1)
        py = self.monitor.top + ny * (self.monitor.height - 1)
        ax = round((px - self.virtual.left) * 65535 / max(1, self.virtual.width - 1))
        ay = round((py - self.virtual.top) * 65535 / max(1, self.virtual.height - 1))
        return ax, ay

    def move(self, nx: float, ny: float) -> None:
        ax, ay = self._abs(nx, ny)
        wi.send([wi.mouse(ax, ay, 0, MOVE | ABSOLUTE | VIRTUALDESK)])

    def button(self, button: int, down: bool, nx: float | None = None, ny: float | None = None) -> None:
        if button not in _BUTTONS:
            raise ValueError("botão inválido")
        fdown, fup, data = _BUTTONS[button]
        events = []
        if nx is not None and ny is not None:
            ax, ay = self._abs(nx, ny)
            events.append(wi.mouse(ax, ay, 0, MOVE | ABSOLUTE | VIRTUALDESK))
        events.append(wi.mouse(0, 0, data, fdown if down else fup))
        wi.send(events)
        (self.pressed.add if down else self.pressed.discard)(button)

    def wheel(self, dx: int, dy: int) -> None:
        """dx/dy em unidades do Windows (120 = um "dente" da roda). dy>0 rola para cima."""
        events = []
        dy = max(-1200, min(1200, int(dy)))
        dx = max(-1200, min(1200, int(dx)))
        if dy:
            events.append(wi.mouse(0, 0, dy, WHEEL))
        if dx:
            events.append(wi.mouse(0, 0, dx, HWHEEL))
        wi.send(events)

    def release_all(self) -> None:
        for b in list(self.pressed):
            try:
                self.button(b, False)
            except OSError:
                pass
        self.pressed.clear()
