// HP2P - inicialização do PeerJS (biblioteca oficial 1.5.4, sem alterações).
"use strict";

(function () {
  const cfg = () => window.HP2P_CONFIG;

  async function fetchIceServers() {
    if (!cfg().iceEndpoint) return cfg().fallbackIceServers;
    try {
      const base = cfg().signaling;
      const url = new URL(cfg().iceEndpoint, `${base.secure ? "https" : "http"}://${base.host}:${base.port}`);
      const r = await fetch(url, { cache: "no-store" });
      if (r.ok) {
        const j = await r.json();
        if (Array.isArray(j.iceServers) && j.iceServers.length) return j.iceServers;
      }
    } catch (_) { /* usa o fallback */ }
    return cfg().fallbackIceServers;
  }

  function randomOperatorId() {
    const b = crypto.getRandomValues(new Uint8Array(8));
    return "op-" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  }

  // Cria e registra o peer do controlador. Resolve quando o PeerServer confirma (OPEN).
  async function createPeer(onEvent) {
    const iceServers = await fetchIceServers();
    const s = cfg().signaling;
    const peer = new Peer(randomOperatorId(), {
      host: s.host, port: s.port, path: s.path, key: s.key, secure: s.secure, debug: 1,
      config: { iceServers },
    });
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("Tempo esgotado ao contatar o servidor de sinalização")), 12000);
      peer.once("open", () => { clearTimeout(t); resolve(); });
      peer.once("error", (e) => { clearTimeout(t); reject(e); });
    });
    peer.on("disconnected", () => {
      onEvent && onEvent("signal-lost");
      setTimeout(() => { if (!peer.destroyed && peer.disconnected) peer.reconnect(); }, 2000);
    });
    peer.on("open", () => onEvent && onEvent("signal-ok"));
    peer.on("error", (e) => onEvent && onEvent("error", e));
    return { peer, iceServers };
  }

  window.HP2P = window.HP2P || {};
  window.HP2P.peer = { createPeer };
})();
