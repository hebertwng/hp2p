// HP2P - exibição da tela remota: vídeo com proporção preservada, estatísticas e ajuste de qualidade.
"use strict";

(function () {
  class RemoteScreen {
    constructor(video) {
      this.video = video;
      this.session = null;
      this._stats = null;
      this._last = null;
      this._stable = 0;
      this.onStats = null;
    }

    attach(stream) {
      this.video.srcObject = stream;
      this.video.muted = true;
      const p = this.video.play();
      if (p && p.catch) p.catch(() => {});
    }

    // Converte coordenadas da página em coordenadas normalizadas da IMAGEM (object-fit: contain).
    normalize(clientX, clientY) {
      const v = this.video;
      const rect = v.getBoundingClientRect();
      const vw = v.videoWidth, vh = v.videoHeight;
      if (!vw || !vh) return null;
      const scale = Math.min(rect.width / vw, rect.height / vh);
      const dw = vw * scale, dh = vh * scale;
      const ox = rect.left + (rect.width - dw) / 2, oy = rect.top + (rect.height - dh) / 2;
      const x = (clientX - ox) / dw, y = (clientY - oy) / dh;
      if (x < 0 || y < 0 || x > 1 || y > 1) return null;
      return { x: Math.round(x * 10000) / 10000, y: Math.round(y * 10000) / 10000 };
    }

    startStats(session) {
      this.session = session;
      clearInterval(this._stats);
      this._stats = setInterval(() => this._collect(), 2000);
    }

    stop() {
      clearInterval(this._stats);
      this.video.srcObject = null;
    }

    async _collect() {
      const pc = this.session?.call?.peerConnection;
      if (!pc) return;
      const st = await pc.getStats();
      let inb = null;
      st.forEach((r) => { if (r.type === "inbound-rtp" && r.kind === "video") inb = r; });
      if (!inb) return;
      const now = { lost: inb.packetsLost || 0, recv: inb.packetsReceived || 0, dropped: inb.framesDropped || 0,
                    decoded: inb.framesDecoded || 0, bytes: inb.bytesReceived || 0, t: performance.now() };
      const info = { fps: inb.framesPerSecond || 0, width: inb.frameWidth, height: inb.frameHeight, kbps: 0, loss: 0 };
      if (this._last) {
        const dt = (now.t - this._last.t) / 1000;
        const dRecv = now.recv - this._last.recv, dLost = now.lost - this._last.lost;
        info.kbps = Math.round(((now.bytes - this._last.bytes) * 8) / 1000 / dt);
        info.loss = dRecv + dLost > 0 ? dLost / (dRecv + dLost) : 0;
        // Ajuste automático: perda alta -> pede qualidade menor; estável por ~20 s -> pede maior.
        if (info.loss > 0.03 || now.dropped - this._last.dropped > 4) {
          this.session.send({ t: "quality", hint: "down" });
          this._stable = 0;
        } else if (++this._stable >= 10) {
          this.session.send({ t: "quality", hint: "up" });
          this._stable = 0;
        }
      }
      this._last = now;
      this.onStats && this.onStats(info);
    }
  }

  window.HP2P = window.HP2P || {};
  window.HP2P.RemoteScreen = RemoteScreen;
})();
