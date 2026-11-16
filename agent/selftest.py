"""Diagnóstico do agente:  HP2P.exe --selftest   (resultado em %APPDATA%\\HP2P\\logs\\selftest.log)

Verifica, dentro do executável distribuído, cada dependência usada numa sessão real.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import traceback

log = logging.getLogger("hp2p.selftest")


def run() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        t0 = time.perf_counter()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({(time.perf_counter() - t0) * 1000:.0f} ms)"))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{exc!r}"))
            log.debug(traceback.format_exc())

    def dpapi():
        from security import dpapi_protect, dpapi_unprotect
        data = secrets.token_bytes(32)
        assert dpapi_unprotect(dpapi_protect(data)) == data
        return "ok"

    def auth():
        from password_manager import generate_password, make_verifier
        v = make_verifier(generate_password())
        return f"verificador SRP {len(v['srp']['v'])} chars"

    def capture(prefer):
        def _f():
            from screen_capture import ScreenCapturer
            c = ScreenCapturer(1, prefer=prefer)
            try:
                f = None
                for _ in range(5):
                    f = c.grab(1280)
                return f"{c.backend_name} {f.width}x{f.height}"
            finally:
                c.close()
        return _f

    def vp8():
        import fractions
        import av
        from aiortc.codecs import get_encoder
        from aiortc.rtcrtpparameters import RTCRtpCodecParameters
        enc = get_encoder(RTCRtpCodecParameters(mimeType="video/VP8", clockRate=90000))
        frame = av.VideoFrame(640, 360, "yuv420p")
        frame.pts, frame.time_base = 0, fractions.Fraction(1, 90000)
        payloads, _ = enc.encode(frame)
        return f"{len(payloads)} pacote(s) RTP"

    def qt():
        from PySide6.QtWidgets import QApplication
        from interface import make_icon
        app = QApplication.instance() or QApplication([])  # noqa: F841
        return f"ícone {make_icon().availableSizes()[0].width()}px"

    def signaling():
        from config import Settings
        from signaling import PeerJSSignaling

        async def _go():
            sig = Settings.load().get("signaling")
            s = PeerJSSignaling("hp2p-selftest-" + secrets.token_hex(4), secrets.token_hex(8), sig["host"],
                                int(sig["port"]), sig["path"], sig["key"], bool(sig["secure"]))
            try:
                await asyncio.wait_for(s.start(), 15)
                return f"registrado em {sig['host']}"
            finally:
                await s.close()
        return asyncio.run(_go())

    check("DPAPI (proteção das credenciais)", dpapi)
    check("Argon2id + SRP-6a", auth)
    check("Captura DXGI", capture("dxgi"))
    check("Captura MSS/GDI (reserva)", capture("mss"))
    check("Codificador VP8", vp8)
    check("Interface Qt", qt)
    check("Servidor de sinalização", signaling)

    ok = all(r[1] for r in results)
    for name, passed, detail in results:
        log.info("[%s] %s — %s", "OK" if passed else "FALHA", name, detail)
    log.info("SELFTEST: %s (%d/%d)", "APROVADO" if ok else "FALHOU", sum(r[1] for r in results), len(results))
    return 0 if ok else 1
