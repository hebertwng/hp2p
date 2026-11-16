// Executa o MESMO authentication.js do navegador dentro do Node, para testar a interoperabilidade
// com o agente Python (pysrp). Protocolo de teste: linhas JSON em stdin/stdout.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const readline = require("readline");

const web = path.join(__dirname, "..", "web-controller");
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(path.join(web, "js/vendor/hash-wasm-4.12.0.umd.min.js"), "utf8"));
vm.runInThisContext(fs.readFileSync(path.join(web, "js/authentication.js"), "utf8"));

const { SrpClient } = window.HP2P.auth;
const rl = readline.createInterface({ input: process.stdin });
let client = null;
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");

rl.on("line", async (line) => {
  const cmd = JSON.parse(line);
  try {
    if (cmd.op === "hello") { client = new SrpClient(); out({ A: client.helloA() }); }
    else if (cmd.op === "prove") {
      const t0 = Date.now();
      const proof = await client.prove(cmd.challenge, cmd.password, cmd.fpAgent, cmd.fpCtrl);
      out({ proof, ms: Date.now() - t0 });
    } else if (cmd.op === "verify") {
      const key = await client.verifyAgent(cmd.ok, cmd.sid, cmd.fpAgent, cmd.fpCtrl);
      out({ agentVerified: !!key });
    } else if (cmd.op === "quit") { process.exit(0); }
  } catch (e) { out({ error: String(e && e.message || e) }); }
});
