"""Gera o controlador web em UM arquivo HTML (CSS, JS, PeerJS e hash-wasm embutidos).

O arquivo funciona aberto direto do disco (duplo clique), sem servidor web: a sinalização é feita
pelo PeerJS Cloud definido em web-controller/config.js.

    agent\\.venv\\Scripts\\python build\\build_web.py      ->  dist\\HP2P-Acesso.html
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web-controller"
OUT = ROOT / "dist" / "HP2P-Acesso.html"


def inline_script(code: str) -> str:
    return "<script>\n" + code.replace("</script", "<\\/script") + "\n</script>"


def build() -> Path:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "css" / "style.css").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="css/style.css">', f"<style>\n{css}\n</style>")

    def repl(m: re.Match) -> str:
        src = m.group(1)
        return inline_script((WEB / src).read_text(encoding="utf-8"))

    # <script src="..." [integrity=...] [defer]></script>  ->  script embutido (SRI não se aplica a conteúdo inline)
    html, n = re.subn(r'<script src="([^"]+)"[^>]*></script>', repl, html)
    if n < 9:
        raise SystemExit(f"esperava 9 scripts, encontrei {n}")
    # app.js usava defer: embutido no fim do <head> ele rodaria antes do <body>; move para o fim do body
    app = inline_script((WEB / "js" / "app.js").read_text(encoding="utf-8"))
    html = html.replace(app, "")
    html = html.replace("</body>", app + "\n</body>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[ok] {OUT} ({OUT.stat().st_size // 1024} KB)")
    return OUT


if __name__ == "__main__":
    build()
