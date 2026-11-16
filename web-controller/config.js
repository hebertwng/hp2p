// Configuração da página de acesso HP2P: sinalização pelo PeerJS Cloud público (0.peerjs.com).
window.HP2P_CONFIG = {
  signaling: { host: "0.peerjs.com", port: 443, path: "/", key: "peerjs", secure: true },
  iceEndpoint: null,
  // STUN do Google + TURN públicos do projeto PeerJS (os mesmos usados pelo HP2P.exe)
  fallbackIceServers: [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: ["turn:eu-0.turn.peerjs.com:3478", "turn:us-0.turn.peerjs.com:3478"], username: "peerjs", credential: "peerjsp" },
  ],
  protocolVersion: 1,
};
