"""Captura de tela em thread dedicada, com dois backends.

Avaliação (item 3.5) e escolha:
  * DXGI Desktop Duplication (dxcam): captura pela GPU, ~0,1 ms por chamada e só entrega quadro
    quando a tela muda. Backend PADRÃO.
  * MSS (GDI/BitBlt): sem dependência de GPU; medido em ~58 ms por quadro 1080p nesta máquina
    de testes. Usado automaticamente como RESERVA (sessão RDP, driver sem duplicação etc.).
  * Windows Graphics Capture: também via GPU, exige WinRT; fica como evolução (mesma interface).
  * Codificação: VP8 (libvpx via PyAV/FFmpeg, embutido no aiortc) — ver video_encoder.py.

Monitores: o índice 1 é SEMPRE o monitor principal; 2, 3… são os demais (ordem do Windows).
O mesmo retângulo é usado para converter as coordenadas do mouse (mouse_control.py).

Limitações: a área de trabalho segura (UAC, Ctrl+Alt+Del, tela de bloqueio) não pode ser capturada
por um processo comum; nesses momentos o último quadro é repetido.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass

import av
import mss
import numpy as np

log = logging.getLogger("hp2p.capture")

try:  # backend opcional
    import dxcam
except Exception:  # noqa: BLE001
    dxcam = None


@dataclass(frozen=True)
class Monitor:
    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool = False


class _CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


# Seta do cursor desenhada no quadro (nenhum dos backends inclui o ponteiro).
_ARROW = [
    "X..........", "XX.........", "XOX........", "XOOX.......", "XOOOX......", "XOOOOX.....",
    "XOOOOOX....", "XOOOOOOX...", "XOOOOOOOX..", "XOOOOOOOOX.", "XOOOOOXXXXX", "XOOXOOX....",
    "XOX.XOOX...", "XX..XOOX...", "X....XOOX..", ".....XOOX..", "......XX...",
]
_ARROW_OUT = np.array([[c == "X" for c in row] for row in _ARROW])
_ARROW_IN = np.array([[c == "O" for c in row] for row in _ARROW])


def _new_mss():
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def list_monitors() -> list[Monitor]:
    with _new_mss() as s:
        mons = [m for m in s.monitors[1:]]
    mons.sort(key=lambda m: (not m.get("is_primary", m["left"] == 0 and m["top"] == 0), m["left"], m["top"]))
    return [Monitor(i + 1, m["left"], m["top"], m["width"], m["height"], i == 0) for i, m in enumerate(mons)]


def virtual_screen() -> Monitor:
    with _new_mss() as s:
        m = s.monitors[0]
    return Monitor(0, m["left"], m["top"], m["width"], m["height"])


class _DxgiBackend:
    name = "DXGI"

    def __init__(self, mon: Monitor):
        if dxcam is None:
            raise RuntimeError("dxcam indisponível")
        self.cam = None
        for dev in range(4):
            for out in range(8):
                try:
                    cam = dxcam.create(device_idx=dev, output_idx=out, output_color="BGRA")
                except Exception:  # noqa: BLE001
                    break
                r = cam._output.desc.DesktopCoordinates
                if (r.left, r.top) == (mon.left, mon.top):
                    self.cam = cam
                    break
                cam.release()
            if self.cam:
                break
        if self.cam is None:
            raise RuntimeError("saída DXGI do monitor não encontrada")
        self.last: np.ndarray | None = None

    def grab(self) -> np.ndarray:
        frame = self.cam.grab()
        if frame is not None:
            self.last = frame
        if self.last is None:  # primeira chamada sem mudança na tela: força um quadro
            for _ in range(50):
                frame = self.cam.grab()
                if frame is not None:
                    self.last = frame
                    break
                threading.Event().wait(0.01)
            if self.last is None:
                raise RuntimeError("DXGI não entregou quadro")
        return self.last

    def close(self) -> None:
        try:
            self.cam.release()
        except Exception:  # noqa: BLE001
            pass


class _MssBackend:
    name = "MSS/GDI"

    def __init__(self, mon: Monitor):
        self.sct = _new_mss()
        self.region = {"left": mon.left, "top": mon.top, "width": mon.width, "height": mon.height}

    def grab(self) -> np.ndarray:
        shot = self.sct.grab(self.region)
        return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def close(self) -> None:
        self.sct.close()


class ScreenCapturer:
    """Toda a captura acontece numa única thread (as APIs de captura não são thread-safe)."""

    def __init__(self, monitor_index: int = 1, draw_cursor: bool = True, prefer: str = "dxgi"):
        self.draw_cursor = draw_cursor
        self.prefer = prefer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hp2p-capture")
        mons = list_monitors()
        self.monitor_info = next((m for m in mons if m.index == monitor_index), mons[0])
        self._backend = None
        self._last: av.VideoFrame | None = None
        self._failures = 0

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "-"

    def monitor(self) -> Monitor:
        return self.monitor_info

    def virtual_screen(self) -> Monitor:
        return virtual_screen()

    def _ensure_backend(self) -> None:
        if self._backend is not None:
            return
        if self.prefer == "dxgi":
            try:
                self._backend = _DxgiBackend(self.monitor_info)
            except Exception as exc:  # noqa: BLE001
                log.warning("Captura DXGI indisponível (%s); usando MSS/GDI", exc)
        if self._backend is None:
            self._backend = _MssBackend(self.monitor_info)
        log.info("Captura de tela: %s, monitor %d (%dx%d em %d,%d)", self._backend.name,
                 self.monitor_info.index, self.monitor_info.width, self.monitor_info.height,
                 self.monitor_info.left, self.monitor_info.top)

    async def grab_async(self, loop, max_width: int) -> av.VideoFrame:
        return await loop.run_in_executor(self._executor, self._grab, max_width)

    def grab(self, max_width: int) -> av.VideoFrame:
        return self._executor.submit(self._grab, max_width).result()

    def _grab(self, max_width: int) -> av.VideoFrame:
        try:
            self._ensure_backend()
            arr = self._backend.grab()
            self._failures = 0
        except Exception as exc:  # noqa: BLE001
            # desktop seguro, troca de resolução, sessão bloqueada: recria o backend e repete o quadro
            self._failures += 1
            log.debug("falha de captura (%s)", exc)
            if self._backend is not None:
                self._backend.close()
                self._backend = None
                if self._failures >= 3:
                    self.prefer = "mss"
            if self._last is not None:
                return self._last
            raise
        if self.draw_cursor:
            arr = self._with_cursor(arr)
        frame = av.VideoFrame.from_ndarray(arr, format="bgra")
        w, h = scaled_size(arr.shape[1], arr.shape[0], max_width)
        out = frame.reformat(width=w, height=h, format="yuv420p", interpolation="FAST_BILINEAR")
        self._last = out
        return out

    def _with_cursor(self, arr: np.ndarray) -> np.ndarray:
        try:
            ci = _CursorInfo()
            ci.cbSize = ctypes.sizeof(_CursorInfo)
            if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)) or not (ci.flags & 1):
                return arr
            x = ci.ptScreenPos.x - self.monitor_info.left
            y = ci.ptScreenPos.y - self.monitor_info.top
            if x < 0 or y < 0 or x >= arr.shape[1] or y >= arr.shape[0]:
                return arr
            h, w = _ARROW_OUT.shape
            hh, ww = min(h, arr.shape[0] - y), min(w, arr.shape[1] - x)
            arr = arr.copy() if not arr.flags.writeable else arr.copy()
            region = arr[y:y + hh, x:x + ww]
            region[_ARROW_OUT[:hh, :ww]] = (0, 0, 0, 255)
            region[_ARROW_IN[:hh, :ww]] = (255, 255, 255, 255)
        except Exception:  # noqa: BLE001
            pass
        return arr

    def close(self) -> None:
        def _close():
            if self._backend:
                self._backend.close()
                self._backend = None
        try:
            self._executor.submit(_close).result(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)


def scaled_size(width: int, height: int, max_width: int) -> tuple[int, int]:
    if width > max_width:
        height = round(height * max_width / width)
        width = max_width
    return width - (width % 2), height - (height % 2)  # yuv420p exige dimensões pares
