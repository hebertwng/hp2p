// HP2P - controlador web: integra a interface com PeerJS, autenticação, tela, mouse e teclado.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const el = {
    connectView: $("connect-view"), sessionView: $("session-view"), form: $("connect-form"),
    deviceId: $("device-id"), password: $("password"), operator: $("operator"), togglePw: $("toggle-pw"),
    connectBtn: $("connect-btn"), status: $("status"),
    target: $("tb-target"), perm: $("tb-perm"), info: $("tb-info"), dot: $("conn-dot"),
    quality: $("quality"), shortcuts: $("shortcuts"), fullscreen: $("fullscreen-btn"), disconnect: $("disconnect-btn"),
    surface: $("surface"), video: $("remote-video"), overlay: $("overlay"), overlayText: $("overlay-text"),
    overlayBack: $("overlay-back"), toast: $("toast"),
  };
  const PERM_TEXT = { view: "Somente visualização", mouse: "Mouse", full: "Mouse e teclado" };

  let peerCtx = null;    // { peer }
  let session = null;
  let stats = { rtt: null, fps: 0, kbps: 0, w: 0, h: 0 };
  const screen = new HP2P.RemoteScreen(el.video);
  const send = (m) => session && session.send(m);
  const mouse = new HP2P.MouseCapture(el.surface, screen, send);
  const keyboard = new HP2P.KeyboardCapture(el.surface, send);

  // ------------------------------------------------------------ util
  function setStatus(text, kind = "info") { el.status.textContent = text; el.status.className = "status " + kind; }
  let toastTimer = 0;
  function toast(text) {
    el.toast.textContent = text; el.toast.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (el.toast.hidden = true), 5000);
  }
  function formatId(v) {
    const d = v.replace(/\D/g, "").slice(0, 9);
    return [d.slice(0, 3), d.slice(3, 6), d.slice(6, 9)].filter(Boolean).join("-");
  }
  function store(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
  function load(k) { try { return localStorage.getItem(k) || ""; } catch (_) { return ""; } }

  el.deviceId.addEventListener("input", () => { el.deviceId.value = formatId(el.deviceId.value); });
  el.togglePw.addEventListener("click", () => {
    const show = el.password.type === "password";
    el.password.type = show ? "text" : "password";
    el.togglePw.textContent = show ? "Ocultar" : "Mostrar";
    el.togglePw.setAttribute("aria-label", show ? "Ocultar senha" : "Mostrar senha");
  });
  el.operator.value = load("hp2p-operator");
  const qId = new URLSearchParams(location.search).get("id");
  if (qId) el.deviceId.value = formatId(qId);

  function renderInfo() {
    const parts = [];
    if (stats.w) parts.push(`${stats.w}×${stats.h}`);
    if (session?.screen) parts.push(`remoto ${session.screen.width}×${session.screen.height}`);
    if (stats.fps) parts.push(`${Math.round(stats.fps)} fps`);
    if (stats.kbps) parts.push(`${stats.kbps} kbps`);
    if (stats.rtt != null) parts.push(`RTT ${Math.round(stats.rtt)} ms`);
    if (stats.route) parts.push(stats.route);
    el.info.textContent = parts.join(" · ") || "—";
  }

  // ------------------------------------------------------------ conexão
  el.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const digits = el.deviceId.value.replace(/\D/g, "");
    const password = el.password.value;
    const name = el.operator.value.trim() || "Operador";
    if (digits.length !== 9) return setStatus("Informe os 9 dígitos da identificação do computador.", "err");
    if (!password) return setStatus("Informe a senha de acesso.", "err");
    store("hp2p-operator", el.operator.value.trim());
    el.connectBtn.disabled = true;
    setStatus("Conectando ao servidor...", "info");
    try {
      if (!peerCtx || peerCtx.peer.destroyed) {
        peerCtx = await HP2P.peer.createPeer((ev) => {
          if (ev === "signal-lost") el.dot.className = "dot warn";
          if (ev === "signal-ok") el.dot.className = "dot ok";
        });
      }
      session = new HP2P.Session(peerCtx.peer, {
        status: setStatus,
        onStream: (stream) => { screen.attach(stream); },
        onInfo: (i) => { if (i.rtt != null) stats.rtt = i.rtt; renderInfo(); },
        onWarn: toast,
        onEnd: (reason) => endSession(reason),
      });
      await session.start(digits, password, name);
      el.password.value = "";
      enterSession(digits);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      setStatus(msg, "err");
      if (session) { const s = session; session = null; s.end(msg, true); }
    } finally {
      el.connectBtn.disabled = false;
    }
  });

  function enterSession(digits) {
    el.connectView.hidden = true;
    el.sessionView.hidden = false;
    el.target.textContent = formatId(digits);
    el.perm.textContent = PERM_TEXT[session.permission] || session.permission;
    el.overlay.hidden = false;
    el.overlayBack.hidden = true;
    el.overlayText.textContent = "Aguardando a imagem do computador remoto…";
    mouse.enabled = session.permission !== "view";
    keyboard.enabled = session.permission === "full";
    el.surface.style.cursor = mouse.enabled ? "crosshair" : "default";
    el.shortcuts.disabled = !keyboard.enabled;
    screen.onStats = (i) => { stats.fps = i.fps; stats.kbps = i.kbps; stats.w = i.width; stats.h = i.height; renderInfo(); };
    screen.startStats(session);
    setTimeout(async () => {
      const r = await session?.route();
      if (r) { stats.route = r.relay ? "via TURN" : `P2P ${r.local}⇄${r.remote}`; renderInfo(); }
    }, 3000);
    el.surface.focus({ preventScroll: true });
  }

  el.video.addEventListener("playing", () => { el.overlay.hidden = true; });

  function endSession(reason) {
    keyboard.releaseAll();
    mouse.enabled = keyboard.enabled = false;
    screen.stop();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    keyboard.unlock();
    session = null;
    stats = { rtt: null, fps: 0, kbps: 0, w: 0, h: 0 };
    if (!el.sessionView.hidden) {
      el.overlay.hidden = false;
      el.overlayText.textContent = reason;
      el.overlayBack.hidden = false;
      el.dot.className = "dot err";
    } else {
      setStatus(reason, "err");
    }
  }

  el.overlayBack.addEventListener("click", () => {
    el.sessionView.hidden = true;
    el.connectView.hidden = false;
    el.dot.className = "dot ok";
    setStatus("Sessão encerrada.", "info");
    el.password.focus();
  });

  el.disconnect.addEventListener("click", () => {
    if (session) session.end("Você encerrou a sessão.", true);
  });

  el.quality.addEventListener("change", () => send({ t: "quality", level: el.quality.value }));
  el.shortcuts.addEventListener("change", () => {
    if (el.shortcuts.value) keyboard.shortcut(el.shortcuts.value);
    el.shortcuts.value = "";
    el.surface.focus({ preventScroll: true });
  });
  el.fullscreen.addEventListener("click", async () => {
    if (document.fullscreenElement) { await document.exitFullscreen(); return; }
    await el.sessionView.requestFullscreen().catch(() => {});
    await keyboard.lock();
    el.surface.focus({ preventScroll: true });
  });
  document.addEventListener("fullscreenchange", () => {
    el.fullscreen.textContent = document.fullscreenElement ? "Sair da tela cheia" : "Tela cheia";
    if (!document.fullscreenElement) keyboard.unlock();
  });
  window.addEventListener("beforeunload", () => { if (session) session.end("fechando", true); });

  window.HP2P.app = { get session() { return session; } };
})();
