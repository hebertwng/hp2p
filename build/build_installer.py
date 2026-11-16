"""Gera o instalador do HP2P (Inno Setup 6): agente + página de acesso, sinalização pelo PeerJS Cloud.

    agent\\.venv\\Scripts\\python build\\build_installer.py
    ... --skip-agent   reaproveita dist\\HP2P já gerado

Saída: dist\\HP2P-Instalador-<versão>.exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "build"))


def find_iscc() -> str:
    cands = [shutil.which("ISCC.exe") or "",
             os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
             r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise SystemExit("Inno Setup 6 não encontrado. Instale: winget install JRSoftware.InnoSetup --scope user")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-agent", action="store_true")
    a = ap.parse_args()
    from config import APP_VERSION
    import build_web

    build_web.build()
    if not a.skip_agent or not (ROOT / "dist" / "HP2P" / "HP2P.exe").exists():
        subprocess.run([sys.executable, str(ROOT / "build" / "build_agent.py"), "--cloud"], check=True)
    iss = ROOT / "build" / "installer" / "hp2p.iss"
    print("[..] Inno Setup")
    subprocess.run([find_iscc(), "/Q", f"/DAppVersion={APP_VERSION}", str(iss)], check=True)
    out = ROOT / "dist" / f"HP2P-Instalador-{APP_VERSION}.exe"
    print(f"[ok] {out} ({out.stat().st_size / 1024 / 1024:.0f} MB)")


if __name__ == "__main__":
    main()
