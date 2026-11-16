// HP2P - fluxo da sessão: localizar o agente, autenticar (SRP), aguardar autorização local,
// aceitar a mídia (com verificação de fingerprint) e manter a conexão viva.
"use strict";

(function () {
  const { SrpClient, sdpFingerprint } = window.HP2P.auth;

  const ERR_TEXT = {
    "wrong-password": "Senha incorreta.",
    locked: "Computador bloqueado temporariamente por excesso de tentativas.",
    "rate-limit": "Muitas tentativas. Aguarde um minuto.",
    "channel-binding": "Falha de segurança: o canal não é o mesmo nos dois lados (possível interceptação).",
    replay: "Desafio inválido. Tente novamente.",
    timeout: "Tempo de autenticação esgotado.",
    "password-changed": "A senha foi alterada no computador remoto. Peça a nova senha.",
    "no-password": "O computador remoto não tem senha ativa.",
    version: "Versão incompatível do agente.",
    "bad-request": "Requisição inválida.",
  };

  class Session {
    constructor(peer, ui) {
      this.peer = peer;
      this.ui = ui;            // { status(text, kind), onStream(stream), onEnd(reason), onInfo(obj), onWarn(text) }
      this.conn = null;
      this.call = null;
      this.permission = "view";
      this.screen = null;
      this.closed = false;
      this._waiters = [];
      this._pendingCalls = new Map();
      this._mediaOffer = null;
      this._ping = null;
      this.rtt = null;
      peer.on("call", (call) => this._onCall(call));
    }

    send(obj) { if (this.conn && this.conn.open) this.conn.send(obj); }

    _wait(types, ms) {
      return new Promise((resolve, reject) => {
        const w = { types, resolve, reject };
        w.timer = setTimeout(() => { this._waiters = this._waiters.filter((x) => x !== w); reject(new Error("Tempo esgotado aguardando o computador remoto")); }, ms);
        this._waiters.push(w);
      });
    }

    _onData(msg) {
      if (!msg || typeof msg !== "object") return;
      const w = this._waiters.find((x) => x.types.includes(msg.t));
      if (w) { clearTimeout(w.timer); this._waiters = this._waiters.filter((x) => x !== w); w.resolve(msg); return; }
      switch (msg.t) {
        case "pong": if (msg.ts) this.rtt = performance.now() - msg.ts; this.ui.onInfo({ rtt: this.rtt }); break;
        case "media-offer": this._mediaOffer = msg; this._tryMedia(); break;
        case "bye": this.end(msg.reason === "local" ? "O usuário remoto encerrou a sessão." :
                              msg.reason === "timeout" ? "Conexão perdida (tempo esgotado)." :
                              msg.reason === "media-binding" ? "Sessão encerrada por falha de segurança na mídia." :
                              "Sessão encerrada pelo computador remoto.", false); break;
        case "warn": this.ui.onWarn(msg.text || "Aviso do agente"); break;
        case "busy": this.end("O computador remoto já está em outra sessão.", false); break;
      }
    }

    async start(deviceDigits, password, operatorName) {
      const target = "hp2p-" + deviceDigits;
      this.ui.status("Localizando o computador...", "info");
      const conn = this.peer.connect(target, { serialization: "json", reliable: true, metadata: { name: operatorName } });
      this.conn = conn;
      await new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error("Não foi possível conectar ao computador (verifique o ID e a rede).")), 30000);
        conn.on("open", () => { clearTimeout(t); resolve(); });
        const onErr = (e) => { if (e.type === "peer-unavailable") { clearTimeout(t); reject(new Error("Computador não encontrado ou offline. Confira o ID.")); } };
        this.peer.on("error", onErr);
        conn.on("error", (e) => { clearTimeout(t); reject(e); });
      });
      conn.on("data", (m) => this._onData(m));
      conn.on("close", () => this.end("Conexão encerrada.", false));
      this._watchPc(conn.peerConnection);

      // ---------------- autenticação SRP-6a + vínculo ao canal DTLS
      const fpCtrl = sdpFingerprint(conn.peerConnection.localDescription.sdp);
      const fpAgent = sdpFingerprint(conn.peerConnection.remoteDescription.sdp);
      const srp = new SrpClient();
      this.ui.status("Autenticando...", "info");
      this.send({ t: "hello", v: window.HP2P_CONFIG.protocolVersion, name: operatorName, A: srp.helloA() });
      const ch = await this._wait(["challenge", "auth-fail", "busy"], 15000);
      if (ch.t === "busy") throw new Error("O computador remoto já está em outra sessão.");
      if (ch.t === "auth-fail") throw new Error(this._failText(ch));
      const proof = await srp.prove(ch, password, fpAgent, fpCtrl,
        (i, n) => this.ui.status(n > 1 ? `Verificando a senha (${i}/${n})...` : "Verificando a senha...", "info"));
      this.send(proof);
      const res = await this._wait(["auth-ok", "auth-fail"], 30000);
      if (res.t === "auth-fail") throw new Error(this._failText(res));
      const key = await srp.verifyAgent(res, ch.sid, fpAgent, fpCtrl);
      if (!key) throw new Error("O computador remoto não comprovou sua identidade. Conexão abortada por segurança.");

      // ---------------- autorização do usuário local
      this.ui.status("Senha aceita. Aguardando o usuário remoto autorizar o acesso...", "wait");
      const dec = await this._wait(["authorized", "rejected"], 120000);
      if (dec.t === "rejected") throw new Error("O usuário remoto rejeitou a conexão.");
      this.permission = dec.permission;
      this.screen = dec.screen;
      this.ui.onInfo({ permission: dec.permission, screen: dec.screen });
      this.ui.status("Acesso autorizado. Iniciando a transmissão da tela...", "ok");
      this._ping = setInterval(() => this.send({ t: "ping", ts: performance.now() }), 4000);
    }

    _failText(m) {
      let t = ERR_TEXT[m.reason] || "Falha de autenticação.";
      if (m.reason === "wrong-password" && m.remaining !== undefined) t += ` Tentativas restantes antes do bloqueio: ${m.remaining}.`;
      if (m.locked_for) t += ` Bloqueado por ${m.locked_for} s.`;
      return t;
    }

    // ---------------- mídia: só aceita a chamada anunciada pelo canal autenticado
    _onCall(call) {
      if (call.peer !== this.conn?.peer) { call.close(); return; }
      this._pendingCalls.set(call.connectionId, call);
      this._tryMedia();
    }

    async _tryMedia() {
      const offer = this._mediaOffer;
      if (!offer) return;
      const call = this._pendingCalls.get(offer.connectionId);
      if (!call || this.call) return;
      this._pendingCalls.delete(offer.connectionId);
      this.call = call;
      call.on("stream", (stream) => stream && this.ui.onStream(stream));
      call.on("close", () => this.end("Transmissão de tela encerrada.", false));
      call.answer();
      const pc = await this._waitFor(() => call.peerConnection && call.peerConnection.localDescription && call.peerConnection.remoteDescription && call.peerConnection, 10000);
      if (!pc) return this.end("Falha ao negociar a mídia.", true);
      const remoteFp = sdpFingerprint(pc.remoteDescription.sdp);
      if (remoteFp !== String(offer.fp)) {
        return this.end("Falha de segurança: a mídia não veio do computador autenticado.", true);
      }
      pc.addEventListener("track", (ev) => { if (!ev.streams.length) this.ui.onStream(new MediaStream([ev.track])); });
      for (const r of pc.getReceivers()) if (r.track && r.track.kind === "video" && !call.remoteStream) this.ui.onStream(new MediaStream([r.track]));
      this._watchPc(pc, true);
      this.send({ t: "media-answer", connectionId: offer.connectionId, fp: sdpFingerprint(pc.localDescription.sdp) });
    }

    _waitFor(fn, ms) {
      return new Promise((resolve) => {
        const t0 = performance.now();
        const tick = () => { const v = fn(); if (v) return resolve(v); if (performance.now() - t0 > ms) return resolve(null); setTimeout(tick, 50); };
        tick();
      });
    }

    _watchPc(pc, media = false) {
      if (!pc) return;
      pc.addEventListener("connectionstatechange", () => {
        if (pc.connectionState === "failed") this.end("Conexão perdida.", false);
        if (pc.connectionState === "disconnected") this.ui.onWarn(media ? "Instabilidade na conexão de vídeo..." : "Instabilidade na conexão...");
      });
    }

    async route() {
      const pc = this.call?.peerConnection || this.conn?.peerConnection;
      if (!pc) return null;
      const st = await pc.getStats();
      let pair = null;
      st.forEach((r) => { if (r.type === "transport" && r.selectedCandidatePairId) pair = st.get(r.selectedCandidatePairId); });
      if (!pair) st.forEach((r) => { if (r.type === "candidate-pair" && r.nominated && r.state === "succeeded") pair = r; });
      if (!pair) return null;
      const l = st.get(pair.localCandidateId), r = st.get(pair.remoteCandidateId);
      return { local: l?.candidateType, remote: r?.candidateType, relay: l?.candidateType === "relay" || r?.candidateType === "relay" };
    }

    end(reason, notify = true) {
      if (this.closed) return;
      this.closed = true;
      clearInterval(this._ping);
      for (const w of this._waiters) { clearTimeout(w.timer); w.reject(new Error(reason)); }
      this._waiters = [];
      if (notify) this.send({ t: "bye" });
      setTimeout(() => {
        try { this.call && this.call.close(); } catch (_) {}
        try { this.conn && this.conn.close(); } catch (_) {}
      }, notify ? 200 : 0);
      this.ui.onEnd(reason);
    }
  }

  window.HP2P = window.HP2P || {};
  window.HP2P.Session = Session;
})();
