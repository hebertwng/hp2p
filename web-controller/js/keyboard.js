// HP2P - captura de teclado (keydown/keyup) enquanto a tela remota está em foco.
// Não captura quando o operador digita em campos do próprio controlador.
"use strict";

(function () {
  const FORM_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON"]);

  class KeyboardCapture {
    constructor(surface, send) {
      this.surface = surface;
      this.send = send;
      this.enabled = false;
      this.down = new Map(); // code -> key
      document.addEventListener("keydown", (e) => this._on(e, "down"), true);
      document.addEventListener("keyup", (e) => this._on(e, "up"), true);
      window.addEventListener("blur", () => this.releaseAll());
      document.addEventListener("visibilitychange", () => document.hidden && this.releaseAll());
    }

    _active(e) {
      if (!this.enabled) return false;
      const el = document.activeElement;
      if (el && FORM_TAGS.has(el.tagName) && el !== this.surface) return false;
      return el === this.surface || this.surface.contains(el) || document.fullscreenElement;
    }

    _on(e, a) {
      if (!this._active(e) || e.isComposing) return;
      e.preventDefault();
      e.stopPropagation();
      if (a === "down") this.down.set(e.code, e.key);
      else if (!this.down.has(e.code)) return;
      else this.down.delete(e.code);
      this.send({ t: "k", a, code: e.code, key: e.key });
    }

    releaseAll() {
      for (const [code, key] of this.down) this.send({ t: "k", a: "up", code, key });
      this.down.clear();
    }

    shortcut(name) { this.send({ t: "sc", name }); }

    // Em tela cheia, a Keyboard Lock API permite enviar Esc, Alt+Tab e a tecla Windows (Chrome/Edge).
    async lock() {
      try { if (navigator.keyboard && navigator.keyboard.lock) await navigator.keyboard.lock(); } catch (_) {}
    }
    unlock() { try { navigator.keyboard && navigator.keyboard.unlock && navigator.keyboard.unlock(); } catch (_) {} }
  }

  window.HP2P = window.HP2P || {};
  window.HP2P.KeyboardCapture = KeyboardCapture;
})();
