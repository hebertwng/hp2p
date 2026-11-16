"""Gera o executável Windows do agente HP2P (PyInstaller, pasta única, sem console).

    agent\\.venv\\Scripts\\python build\\build_agent.py --server 192.168.0.10 --port 9443
    agent\\.venv\\Scripts\\python build\\build_agent.py --server hp2p.empresa.com.br --port 443 --secure

Saída:
    dist\\HP2P\\HP2P.exe          executável (Python, Qt, aiortc/FFmpeg, numpy... embutidos)
    dist\\HP2P\\settings.json     configuração de fábrica (servidor de sinalização)
    dist\\HP2P-agente.zip         pasta compactada para distribuir

O computador de destino NÃO precisa ter Python nem nenhuma biblioteca instalada.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller"


def make_icon(path: Path) -> None:
    from PySide6.QtWidgets import QApplication
    sys.path.insert(0, str(AGENT))
    from interface import make_icon as icon

    app = QApplication.instance() or QApplication([])  # noqa: F841
    pm = icon().pixmap(256, 256)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pm.save(str(path), "ICO"):
        pm.save(str(path.with_suffix(".png")), "PNG")
        raise SystemExit("Falha ao salvar o ícone .ico")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="localhost")
    ap.add_argument("--port", type=int, default=9443)
    ap.add_argument("--secure", action="store_true")
    ap.add_argument("--key", default="hp2p")
    ap.add_argument("--cloud", action="store_true", help="sinalização pelo PeerJS Cloud (0.peerjs.com)")
    a = ap.parse_args()

    icon = WORK / "hp2p.ico"
    make_icon(icon)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "HP2P", "--windowed", "--onedir",
        "--icon", str(icon),
        "--distpath", str(DIST), "--workpath", str(WORK), "--specpath", str(WORK),
        "--paths", str(AGENT),
        "--collect-submodules", "aiortc", "--collect-data", "aiortc",
        "--collect-submodules", "aioice",
        "--collect-submodules", "dxcam", "--collect-submodules", "comtypes",
        "--hidden-import", "srp._pysrp", "--hidden-import", "argon2.low_level",
        "--exclude-module", "tkinter", "--exclude-module", "comtypes.test",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.Qt3DCore", "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtMultimedia",
        str(AGENT / "main.py"),
    ]
    print("[..] PyInstaller")
    subprocess.run(cmd, check=True, cwd=AGENT)

    out = DIST / "HP2P"
    if a.cloud:
        sig = {"host": "0.peerjs.com", "port": 443, "path": "/", "key": "peerjs", "secure": True}
    else:
        sig = {"host": a.server, "port": a.port, "path": "/signal", "key": a.key, "secure": a.secure}
    (out / "settings.json").write_text(json.dumps({"signaling": sig}, indent=2), encoding="utf-8")
    zip_base = DIST / "HP2P-agente"
    shutil.make_archive(str(zip_base), "zip", DIST, "HP2P")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"[ok] {out / 'HP2P.exe'}  (pasta: {size:.0f} MB)")
    print(f"[ok] {zip_base}.zip ({(zip_base.with_suffix('.zip')).stat().st_size / 1024 / 1024:.0f} MB)")
    print(f"[ok] sinalização de fábrica: {'wss' if sig['secure'] else 'ws'}://{sig['host']}:{sig['port']}{sig['path']}")


if __name__ == "__main__":
    main()
