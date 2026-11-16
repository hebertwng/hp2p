"""Janela-alvo para o teste ponta a ponta de mouse e teclado.

Fica no topo, centralizada no monitor principal. Registra cliques e o texto digitado em um JSON.
Fecha sozinha após Enter ou após o tempo limite.
"""

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

out = Path(sys.argv[1])
app = QApplication([])
w = QWidget(None, Qt.WindowStaysOnTopHint)
w.setWindowTitle("HP2P - alvo de teste de entrada")
lay = QVBoxLayout(w)
lay.addWidget(QLabel("Teste HP2P: este campo recebe o teclado remoto"))
edit = QLineEdit()
edit.setStyleSheet("font-size: 22px;")
lay.addWidget(edit)
w.resize(520, 220)
geo = QGuiApplication.primaryScreen().geometry()
w.move(geo.center().x() - 260, geo.center().y() - 110)
state = {"clicks": 0, "text": None, "focused": False}


class Filter(QWidget):
    pass


def save():
    out.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


orig = edit.mousePressEvent


def on_press(ev):
    state["clicks"] += 1
    save()
    orig(ev)


edit.mousePressEvent = on_press
edit.returnPressed.connect(lambda: (state.update(text=edit.text()), save(), QTimer.singleShot(300, app.quit)))
app.focusChanged.connect(lambda _o, n: (state.update(focused=n is edit), save()))
w.show()
geom = edit.mapToGlobal(edit.rect().center())
state["edit_center"] = [geom.x(), geom.y()]
state["screen"] = [geo.x(), geo.y(), geo.width(), geo.height()]
save()
QTimer.singleShot(60000, app.quit)
app.exec()
save()
