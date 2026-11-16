# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: agente HP2P em UM único executável (onefile, sem console).
# Uso: build_agent.py (define HP2P_ICON) ou  pyinstaller build/hp2p.spec
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
AGENT = os.path.join(ROOT, "agent")
ICON = os.environ.get("HP2P_ICON") or None

hidden = (collect_submodules("aiortc") + collect_submodules("aioice") + collect_submodules("dxcam")
          + [m for m in collect_submodules("comtypes") if not m.startswith("comtypes.test")]
          + ["srp._pysrp", "argon2.low_level", "selftest"])

a = Analysis(
    [os.path.join(AGENT, "main.py")],
    pathex=[AGENT],
    datas=collect_data_files("aiortc"),
    hiddenimports=hidden,
    excludes=["tkinter", "comtypes.test", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtNetwork",
              "PySide6.QtPdf", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtOpenGL", "PySide6.QtSvg", "matplotlib", "PIL", "IPython", "pytest"],
    noarchive=False,
)

# Remove binários do Qt que a interface (QtWidgets) não usa: OpenGL por software, Quick/QML/PDF/rede,
# traduções e plugins opcionais. Mantém: plataforma "windows", estilo e formatos de imagem básicos.
DROP_SUBSTR = ("opengl32sw", "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6Network", "Qt6OpenGL", "Qt6Svg",
               "Qt6VirtualKeyboard", "/translations/", "\\translations\\",
               "plugins/imageformats/qpdf", "plugins\\imageformats\\qpdf",
               "plugins/imageformats/qsvg", "plugins\\imageformats\\qsvg",
               "plugins/iconengines", "plugins\\iconengines",
               "plugins/networkinformation", "plugins\\networkinformation",
               "plugins/tls", "plugins\\tls", "plugins/generic", "plugins\\generic",
               "plugins/platforminputcontexts", "plugins\\platforminputcontexts",
               "qminimal", "qoffscreen", "qdirect2d")


def keep(entry):
    name = entry[0]
    return not any(s.lower() in name.lower() for s in DROP_SUBSTR)


a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="HP2P",
    icon=ICON,
    version=os.environ.get("HP2P_VERSION_FILE") or None,  # descrição/versão (título das notificações)
    console=False,
    debug=False,
    strip=False,
    upx=False,            # UPX aumenta falsos positivos de antivírus
    runtime_tmpdir=None,  # descompacta em %TEMP%\_MEIxxxx e apaga ao sair
)
