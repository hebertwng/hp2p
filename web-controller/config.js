// Configuração do controlador HP2P.
// Padrão: PeerJS Cloud público (0.peerjs.com) — não é preciso servidor próprio.
// Para usar um PeerServer próprio (server/peer-server), troque por:
//   signaling: { host: "seu-servidor", port: 9443, path: "/signal", key: "hp2p", secure: true },
//   iceEndpoint: "/hp2p/ice",
window.HP2P_CONFIG = {
  signaling: { host: "0.peerjs.com", port: 443, path: "/", key: "peerjs", secure: true },
  iceEndpoint: null,
  // STUN do Google + TURN públicos do projeto PeerJS (os mesmos usados pelo agente)
  fallbackIceServers: [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: ["turn:eu-0.turn.peerjs.com:3478", "turn:us-0.turn.peerjs.com:3478"], username: "peerjs", credential: "peerjsp" },
  ],
  protocolVersion: 1,
};
