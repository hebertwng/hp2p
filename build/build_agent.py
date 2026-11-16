"""Gera o agente HP2P como UM único executável Windows (PyInstaller onefile, sem console).

    agent\\.venv\\Scripts\\python build\\build_agent.py

Saída:
    dist\\HP2P.exe   executável único (Python, Qt, aiortc/FFmpeg, numpy... embutidos).
                    Ao abrir, descompacta em %TEMP%\\_MEIxxxx (apagado ao sair).

Padrão de sinalização: PeerJS Cloud (definido em agent/config.py).
O computador de destino NÃO precisa ter Python nem nenhuma biblioteca instalada.
"""

from __future__ import annotations

import os
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
        raise SystemExit("Falha ao salvar o ícone .ico")


def main() -> None:
    icon = WORK / "hp2p.ico"
    make_icon(icon)
    # remove a distribuição antiga em pasta (versões anteriores)
    for old in (DIST / "HP2P", DIST / "HP2P-agente.zip"):
        if old.is_dir():
            shutil.rmtree(old)
        elif old.exists():
            old.unlink()
    env = {**os.environ, "HP2P_ICON": str(icon)}
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", str(DIST), "--workpath", str(WORK), str(ROOT / "build" / "hp2p.spec")]
    print("[..] PyInstaller (onefile)")
    subprocess.run(cmd, check=True, cwd=AGENT, env=env)
    exe = DIST / "HP2P.exe"
    print(f"[ok] {exe} ({exe.stat().st_size / 1024 / 1024:.0f} MB, arquivo único)")


if __name__ == "__main__":
    main()
