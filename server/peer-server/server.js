// HP2P - Servidor de sinalização
//
// * PeerServer OFICIAL (pacote npm "peer") montado em /signal, sem alterações de protocolo.
// * Controlador web estático em "/".
// * GET /hp2p/ice  -> iceServers com credenciais TURN TEMPORÁRIAS (TURN REST API / coturn use-auth-secret).
// * Proteções: chave própria do PeerServer, descoberta de IDs desligada, limite de conexões WS por IP,
//   limite de clientes simultâneos por IP, limite de mensagens por cliente, lista de origens permitidas,
//   cabeçalhos de segurança, HTTPS/WSS.
//
// O servidor NUNCA recebe senhas: a autenticação (SRP-6a) acontece fim-a-fim dentro do DataChannel
// WebRTC (DTLS) entre o navegador e o agente.

"use strict";

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");
const crypto = require("crypto");
const express = require("express");
const { ExpressPeerServer } = require("peer");

// ------------------------------------------------------------------ configuração
const cfgPath = path.join(__dirname, "config.json");
const fileCfg = fs.existsSync(cfgPath) ? JSON.parse(fs.readFileSync(cfgPath, "utf8")) : {};
const env = process.env;
const cfg = {
  port: Number(env.HP2P_PORT || fileCfg.port || 9443),
  peerKey: env.HP2P_PEER_KEY || fileCfg.peerKey || "hp2p",
  tlsKey: env.HP2P_TLS_KEY || fileCfg.tls?.key || "",
  tlsCert: env.HP2P_TLS_CERT || fileCfg.tls?.cert || "",
  proxied: (env.HP2P_PROXIED || String(fileCfg.proxied || false)) === "true",
  allowedOrigins: (env.HP2P_ALLOWED_ORIGINS ? env.HP2P_ALLOWED_ORIGINS.split(",") : fileCfg.allowedOrigins || []).filter(Boolean),
  stunUrls: env.HP2P_STUN ? env.HP2P_STUN.split(",") : fileCfg.stunUrls || ["stun:stun.l.google.com:19302"],
  turnUrls: env.HP2P_TURN_URLS ? env.HP2P_TURN_URLS.split(",") : fileCfg.turn?.urls || [],
  turnSecret: env.HP2P_TURN_SECRET || fileCfg.turn?.secret || "",
  turnTtl: Number(env.HP2P_TURN_TTL || fileCfg.turn?.ttlSeconds || 3600),
  limits: {
    wsPerIpPerMin: 30, maxClientsPerIp: 20, msgsPer10s: 400, icePerIpPerMin: 30,
    ...(fileCfg.limits ? {
      wsPerIpPerMin: fileCfg.limits.wsConnectionsPerIpPerMinute,
      maxClientsPerIp: fileCfg.limits.maxClientsPerIp,
      msgsPer10s: fileCfg.limits.messagesPerClientPer10s,
      icePerIpPerMin: fileCfg.limits.iceRequestsPerIpPerMinute,
    } : {}),
  },
};
const resolveLocal = (p) => (p && !path.isAbsolute(p) ? path.join(__dirname, p) : p);

const ts = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().replace("T", " ").replace("Z", "");
};
const log = (...a) => console.log(`${ts()} [hp2p]`, ...a);

// ------------------------------------------------------------- limitadores simples
class WindowCounter {
  constructor(windowMs) { this.windowMs = windowMs; this.map = new Map(); }
  hit(key) {
    const now = Date.now();
    let e = this.map.get(key);
    if (!e || now - e.start > this.windowMs) { e = { start: now, n: 0 }; this.map.set(key, e); }
    e.n += 1;
    return e.n;
  }
  sweep() { const now = Date.now(); for (const [k, e] of this.map) if (now - e.start > this.windowMs) this.map.delete(k); }
}
const wsRate = new WindowCounter(60_000);
const iceRate = new WindowCounter(60_000);
const msgRate = new WindowCounter(10_000);
const clientsPerIp = new Map();
setInterval(() => { wsRate.sweep(); iceRate.sweep(); msgRate.sweep(); }, 30_000).unref();

function clientIp(req) {
  if (cfg.proxied) {
    const xff = req.headers["x-forwarded-for"];
    if (xff) return String(xff).split(",")[0].trim();
  }
  return (req.socket.remoteAddress || "").replace(/^::ffff:/, "");
}
function originAllowed(origin) {
  if (!cfg.allowedOrigins.length) return true; // sem lista = aceita qualquer origem (modo desenvolvimento)
  return !!origin && cfg.allowedOrigins.includes(origin);
}

// --------------------------------------------------------------------- servidores
const app = express();
app.disable("x-powered-by");
if (cfg.proxied) app.set("trust proxy", 1);

let server;
let scheme = "http";
const pfxPath = resolveLocal(env.HP2P_TLS_PFX || fileCfg.tls?.pfx || "");
if (pfxPath && fs.existsSync(pfxPath)) {
  // Certificado .pfx (ex.: gerado por gerar-certificado.ps1)
  server = https.createServer({ pfx: fs.readFileSync(pfxPath), passphrase: env.HP2P_TLS_PFX_PASS || fileCfg.tls?.pfxPassphrase || "" }, app);
  scheme = "https";
} else if (cfg.tlsKey && cfg.tlsCert && fs.existsSync(resolveLocal(cfg.tlsKey))) {
  server = https.createServer({ key: fs.readFileSync(resolveLocal(cfg.tlsKey)), cert: fs.readFileSync(resolveLocal(cfg.tlsCert)) }, app);
  scheme = "https";
} else {
  server = http.createServer(app);
}

// Limite de handshakes WebSocket por IP e verificação de origem ANTES do PeerServer tratar o upgrade.
server.prependListener("upgrade", (req, socket) => {
  const ip = clientIp(req);
  const n = wsRate.hit(ip);
  const origin = req.headers.origin;
  if (n > cfg.limits.wsPerIpPerMin || (clientsPerIp.get(ip) || 0) >= cfg.limits.maxClientsPerIp || (origin && !originAllowed(origin))) {
    log(`WS recusado de ${ip} (tentativas/min=${n}, origem=${origin || "-"})`);
    socket.write("HTTP/1.1 429 Too Many Requests\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }
  clientsPerIp.set(ip, (clientsPerIp.get(ip) || 0) + 1);
  socket.once("close", () => clientsPerIp.set(ip, Math.max(0, (clientsPerIp.get(ip) || 1) - 1)));
});

// Cabeçalhos de segurança para o controlador web.
app.use((req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader("Content-Security-Policy",
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; " +
    "connect-src 'self' ws: wss: https: http:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'");
  if (scheme === "https") res.setHeader("Strict-Transport-Security", "max-age=31536000");
  next();
});

const peerServer = ExpressPeerServer(server, {
  path: "/",
  key: cfg.peerKey,
  allow_discovery: false, // não expõe a lista de IDs (evita enumeração de computadores)
  alive_timeout: 60_000,
  expire_timeout: 5_000,
  concurrent_limit: 5000,
  proxied: cfg.proxied,
});

peerServer.on("connection", (client) => log(`registrado ${client.getId()}`));
peerServer.on("disconnect", (client) => log(`saiu ${client.getId()}`));
peerServer.on("message", (client, message) => {
  if (message.type === "HEARTBEAT") return;
  const n = msgRate.hit(client.getId());
  if (n > cfg.limits.msgsPer10s) {
    log(`limite de mensagens excedido por ${client.getId()}: desconectando`);
    try { client.getSocket()?.close(); } catch (_) {}
    return;
  }
  if (message.type === "OFFER") log(`OFFER ${client.getId()} -> ${message.dst} (${message.payload?.type || "?"})`);
});
peerServer.on("error", (err) => log("erro:", err?.message || err));

app.use("/signal", peerServer);

// Credenciais TURN temporárias (padrão "TURN REST API", compatível com coturn --use-auth-secret).
app.get("/hp2p/ice", (req, res) => {
  const ip = clientIp(req);
  if (iceRate.hit(ip) > cfg.limits.icePerIpPerMin) return res.status(429).json({ error: "rate-limit" });
  if (!originAllowed(req.headers.origin || "")) {
    if (req.headers.origin) return res.status(403).json({ error: "origin" });
  }
  const iceServers = cfg.stunUrls.map((u) => ({ urls: u }));
  if (cfg.turnUrls.length && cfg.turnSecret) {
    const username = `${Math.floor(Date.now() / 1000) + cfg.turnTtl}:hp2p`;
    const credential = crypto.createHmac("sha1", cfg.turnSecret).update(username).digest("base64");
    iceServers.push({ urls: cfg.turnUrls, username, credential });
  }
  res.setHeader("Cache-Control", "no-store");
  res.json({ iceServers, ttl: cfg.turnTtl });
});

app.get("/hp2p/health", (_req, res) => res.json({ ok: true }));
app.use("/", express.static(path.join(__dirname, "..", "..", "web-controller"), { index: "index.html" }));

server.listen(cfg.port, "0.0.0.0", () => {
  log(`HP2P sinalização em ${scheme}://0.0.0.0:${cfg.port}  (WebSocket ${scheme === "https" ? "wss" : "ws"}://…/signal/peerjs, key="${cfg.peerKey}")`);
  log(`Controlador web: ${scheme}://localhost:${cfg.port}/`);
  log(`TURN: ${cfg.turnUrls.length && cfg.turnSecret ? cfg.turnUrls.join(", ") + " (credenciais temporárias)" : "não configurado (só STUN)"}`);
  if (scheme === "http") log("ATENÇÃO: sem TLS. Use HTTPS/WSS fora da rede de testes (ver docs/installation.md).");
});
