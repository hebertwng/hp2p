// HP2P - autenticação do lado do navegador: SRP-6a (RFC 5054, grupo 2048, SHA-256) + Argon2id.
//
// Espelha exatamente o modo RFC 5054 da biblioteca pysrp usada no agente:
//   k  = H(N | PAD(g))                 u = H(PAD(A) | PAD(B))
//   x  = H(s | H("hp2p:" | P'))        P' = hex(Argon2id(senha, salt_kdf))
//   S  = (B - k·g^x)^(a + u·x) mod N   K = H(S)
//   M  = H(H(N) xor H(PAD(g)) | H(I) | s | A | B | K)       HAMK = H(A | M | K)
// (A, B e S sem padding em M/HAMK/K, como no pysrp.)
// Vínculo ao canal: HMAC-SHA256(K, "HP2P-bind-v1|<papel>|<sid>|<fp agente>|<fp navegador>").
// Hash, HMAC e Argon2id vêm da biblioteca hash-wasm (funciona também fora de HTTPS em testes).
"use strict";

(function () {
  const N_HEX =
    "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050A37329CBB4A099ED8193E0757767A13DD52312AB4B03310D" +
    "CD7F48A9DA04FD50E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B855F97993EC975EEAA80D740ADBF4FF" +
    "747359D041D5C33EA71D281E446B14773BCA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748544523B524B0" +
    "D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9" +
    "DBFBB694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73";
  const N = BigInt("0x" + N_HEX);
  const g = 2n;
  const N_LEN = 256;
  const USER = "hp2p";
  const enc = new TextEncoder();

  // ------------------------------------------------------------------ bytes
  const concat = (...parts) => {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  };
  const hexToBytes = (h) => { if (h.length % 2) h = "0" + h; const b = new Uint8Array(h.length / 2); for (let i = 0; i < b.length; i++) b[i] = parseInt(h.substr(i * 2, 2), 16); return b; };
  const bytesToHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  const toBig = (b) => (b.length ? BigInt("0x" + bytesToHex(b)) : 0n);
  const fromBig = (n) => (n === 0n ? new Uint8Array(0) : hexToBytes(n.toString(16))); // mínimo, big-endian
  const pad = (b, len = N_LEN) => concat(new Uint8Array(Math.max(0, len - b.length)), b);
  const b64 = (b) => btoa(String.fromCharCode(...b));
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const mod = (a, m) => ((a % m) + m) % m;
  function modPow(base, exp, m) {
    let r = 1n; base = mod(base, m);
    while (exp > 0n) { if (exp & 1n) r = (r * base) % m; base = (base * base) % m; exp >>= 1n; }
    return r;
  }
  const equal = (a, b) => { if (a.length !== b.length) return false; let d = 0; for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i]; return d === 0; };

  async function H(...parts) { return hexToBytes(await hashwasm.sha256(concat(...parts))); }
  async function hmac(key, text) {
    const h = await hashwasm.createHMAC(hashwasm.createSHA256(), key);
    h.init(); h.update(enc.encode(text));
    return h.digest("binary");
  }

  const N_BYTES = fromBig(N);
  let constants = null;
  async function consts() {
    if (constants) return constants;
    const k = toBig(await H(N_BYTES, pad(fromBig(g))));
    const hN = await H(N_BYTES), hg = await H(pad(fromBig(g)));
    const hNxg = hN.map((v, i) => v ^ hg[i]);
    const hI = await H(enc.encode(USER));
    constants = { k, hNxg, hI };
    return constants;
  }

  async function kdf(password, p) {
    if (p.alg !== "argon2id") throw new Error("KDF não suportada: " + p.alg);
    if (p.m > 262144 || p.t > 10 || p.p > 4) throw new Error("Parâmetros de KDF fora do limite");
    return hashwasm.argon2id({
      password, salt: unb64(p.salt), parallelism: p.p, iterations: p.t,
      memorySize: p.m, hashLength: p.len, outputType: "hex",
    });
  }

  class SrpClient {
    constructor() {
      const rnd = crypto.getRandomValues(new Uint8Array(32));
      this.a = toBig(rnd);
      this.A = modPow(g, this.a, N);
      this.results = [];
    }
    helloA() { return b64(fromBig(this.A)); }

    // Calcula a prova para cada credencial oferecida pelo agente.
    async prove(challenge, password, fpAgent, fpCtrl, onProgress) {
      if (challenge.group !== "rfc5054-2048" || challenge.hash !== "SHA-256" || challenge.user !== USER) {
        throw new Error("Parâmetros de autenticação incompatíveis");
      }
      const { k, hNxg, hI } = await consts();
      const proofs = [];
      this.results = [];
      let i = 0;
      for (const cred of challenge.creds) {
        onProgress && onProgress(++i, challenge.creds.length);
        const s = unb64(cred.srp.salt);
        const Bb = unb64(cred.srp.B);
        const B = toBig(Bb);
        if (B % N === 0n) throw new Error("Desafio inválido do agente");
        const u = toBig(await H(pad(fromBig(this.A)), pad(Bb)));
        if (u === 0n) throw new Error("Desafio inválido do agente");
        const pPrime = await kdf(password, cred.kdf);
        const x = toBig(await H(s, await H(enc.encode(USER + ":" + pPrime))));
        const S = modPow(mod(B - k * modPow(g, x, N), N), this.a + u * x, N);
        const K = await H(fromBig(S));
        const Ab = fromBig(this.A);
        const M = await H(hNxg, hI, s, Ab, fromBig(B), K);
        const HAMK = await H(Ab, M, K);
        const bind = await hmac(K, `HP2P-bind-v1|controller|${challenge.sid}|${fpAgent}|${fpCtrl}`);
        this.results.push({ K, HAMK });
        proofs.push({ M: b64(M), bind: b64(bind) });
      }
      return { t: "proof", sid: challenge.sid, proofs };
    }

    // Confirma que o AGENTE conhece o verificador (HAMK) e está no mesmo canal DTLS (bind).
    async verifyAgent(ok, sid, fpAgent, fpCtrl) {
      const hamk = unb64(ok.HAMK || "");
      const r = this.results.find((x) => equal(x.HAMK, hamk));
      if (!r) return null;
      const expected = await hmac(r.K, `HP2P-bind-v1|agent|${sid}|${fpAgent}|${fpCtrl}`);
      if (!equal(expected, unb64(ok.bind || ""))) return null;
      return r.K;
    }
  }

  // Fingerprint DTLS mais forte presente no SDP ("sha-512 AB:CD:…"). O aiortc anuncia
  // sha-256/384/512 e o Chrome guarda só a mais forte: usar a mais forte dá o mesmo valor nos dois lados.
  const FP_STRENGTH = { "sha-512": 3, "sha-384": 2, "sha-256": 1 };
  function sdpFingerprint(sdp) {
    const re = /^a=fingerprint:(sha-256|sha-384|sha-512) ([0-9A-Fa-f:]+)\s*$/gim;
    let best = null, m;
    while ((m = re.exec(sdp || ""))) {
      const alg = m[1].toLowerCase();
      if (!best || FP_STRENGTH[alg] > FP_STRENGTH[best[0]]) best = [alg, m[2].toUpperCase()];
    }
    if (!best) throw new Error("SDP sem fingerprint DTLS");
    return `${best[0]} ${best[1]}`;
  }

  window.HP2P = window.HP2P || {};
  window.HP2P.auth = { SrpClient, sdpFingerprint, _internals: { H, modPow, toBig, fromBig, pad, N, g } };
})();
