// HP2P - captura de mouse sobre a tela remota (coordenadas normalizadas 0..1).
"use strict";

(function () {
  class MouseCapture {
    constructor(surface, screen, send) {
      this.surface = surface;
      this.screen = screen;
      this.send = send;
      this.enabled = false;
      this._pending = null;
      this._raf = 0;
      this._wheel = { x: 0, y: 0 };
      this._handlers = {
        move: (e) => this._move(e),
        down: (e) => this._button(e, "down"),
        up: (e) => this._button(e, "up"),
        wheel: (e) => this._onWheel(e),
        menu: (e) => this.enabled && e.preventDefault(),
      };
      surface.addEventListener("pointermove", this._handlers.move);
      surface.addEventListener("pointerdown", this._handlers.down);
      surface.addEventListener("pointerup", this._handlers.up);
      surface.addEventListener("wheel", this._handlers.wheel, { passive: false });
      surface.addEventListener("contextmenu", this._handlers.menu);
      surface.addEventListener("auxclick", (e) => this.enabled && e.preventDefault());
    }

    _move(e) {
      if (!this.enabled) return;
      const p = this.screen.normalize(e.clientX, e.clientY);
      if (!p) return;
      this._pending = p;
      if (!this._raf) this._raf = requestAnimationFrame(() => {   // no máximo 1 movimento por quadro
        this._raf = 0;
        if (this._pending) this.send({ t: "m", a: "move", x: this._pending.x, y: this._pending.y });
        this._pending = null;
      });
    }

    _button(e, a) {
      if (!this.enabled) return;
      const p = this.screen.normalize(e.clientX, e.clientY);
      if (!p && a === "down") return;
      e.preventDefault();
      this.surface.focus({ preventScroll: true });
      if (a === "down") this.surface.setPointerCapture?.(e.pointerId);
      const msg = { t: "m", a, b: e.button };
      if (p) { msg.x = p.x; msg.y = p.y; }
      this.send(msg);
    }

    _onWheel(e) {
      if (!this.enabled) return;
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;  // linhas/páginas -> pixels
      // 100 px de rolagem no navegador ~ 1 "dente" (120) no Windows; dy>0 = para cima no Windows
      this._wheel.y += (-e.deltaY * unit * 120) / 100;
      this._wheel.x += (e.deltaX * unit * 120) / 100;
      const dy = Math.trunc(this._wheel.y / 30) * 30, dx = Math.trunc(this._wheel.x / 30) * 30;
      if (dy || dx) {
        this._wheel.y -= dy; this._wheel.x -= dx;
        this.send({ t: "m", a: "wheel", dx, dy });
      }
    }
  }

  window.HP2P = window.HP2P || {};
  window.HP2P.MouseCapture = MouseCapture;
})();
