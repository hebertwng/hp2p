"""Trilha de vídeo WebRTC com a tela capturada (MediaStream) e controle de qualidade.

Codec: VP8 (libvpx via PyAV/FFmpeg), negociado pelo aiortc com o navegador. O aiortc também oferece
H.264 (libx264) — a escolha final é da negociação SDP; o Chrome aceita os dois.

Controle de qualidade:
  * presets (low/medium/high) definem largura máxima e FPS;
  * modo "auto": o navegador mede perda/quadros descartados e pede ajuste pelo DataChannel, e o
    próprio agente reduz o FPS quando a captura+conversão não acompanha o intervalo alvo;
  * bitrate: o aiortc ajusta pelo REMB do navegador; aqui só ampliamos o teto padrão (1,5 Mbps).
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import time

from aiortc import MediaStreamTrack
from aiortc.codecs import vpx

from screen_capture import ScreenCapturer

log = logging.getLogger("hp2p.video")

VIDEO_CLOCK = 90000
TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK)

PRESETS = {
    "low": {"max_width": 960, "fps": 8},
    "medium": {"max_width": 1280, "fps": 15},
    "high": {"max_width": 1920, "fps": 24},
}


def configure_bitrate(max_bitrate: int) -> None:
    vpx.MAX_BITRATE = max(vpx.MIN_BITRATE, int(max_bitrate))
    vpx.DEFAULT_BITRATE = min(1_500_000, vpx.MAX_BITRATE)


class ScreenVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, capturer: ScreenCapturer, quality: str = "auto"):
        super().__init__()
        self.capturer = capturer
        self.auto = quality == "auto"
        self.preset = "medium" if self.auto else quality
        self.max_width = PRESETS[self.preset]["max_width"]
        self.fps = PRESETS[self.preset]["fps"]
        self.gate = asyncio.Event()      # só libera quadros após o vínculo da mídia ser verificado
        self._start: float | None = None
        self._next = 0.0
        self.frames = 0
        self._slow = 0

    def set_quality(self, level: str, manual: bool = True) -> None:
        if level == "auto":
            self.auto = True
            return
        if level in PRESETS:
            if manual:
                self.auto = False
            self.preset = level
            self.max_width = PRESETS[level]["max_width"]
            self.fps = PRESETS[level]["fps"]
            log.info("Qualidade de vídeo: %s (%dpx, %d fps)", level, self.max_width, self.fps)

    def suggest(self, direction: str) -> None:
        """Ajuste automático pedido pelo navegador ("down"/"up")."""
        if not self.auto:
            return
        order = ["low", "medium", "high"]
        i = order.index(self.preset)
        j = max(0, i - 1) if direction == "down" else min(len(order) - 1, i + 1)
        if j != i:
            self.set_quality(order[j], manual=False)

    async def recv(self):
        await self.gate.wait()
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        if self._start is None:
            self._start = now
            self._next = now
        interval = 1.0 / self.fps
        self._next = max(self._next + interval, now)  # nunca acumula atraso
        wait = self._next - now
        if wait > 0:
            await asyncio.sleep(wait)
        t0 = time.monotonic()
        frame = await self.capturer.grab_async(loop, self.max_width)
        spent = time.monotonic() - t0
        # autoajuste local: se capturar+converter consome >80% do intervalo, reduz a qualidade
        if self.auto:
            self._slow = self._slow + 1 if spent > interval * 0.8 else 0
            if self._slow >= self.fps * 2:
                self._slow = 0
                self.suggest("down")
        frame.pts = int((time.monotonic() - self._start) * VIDEO_CLOCK)
        frame.time_base = TIME_BASE
        self.frames += 1
        return frame
