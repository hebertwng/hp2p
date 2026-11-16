"""Testes unitários do mapeamento de mouse/teclado (sem injetar eventos: SendInput é interceptado)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import win_input  # noqa: E402

sent = []
win_input.send = lambda inputs: sent.extend(inputs) or len(inputs)

import keyboard_control as kc  # noqa: E402
import mouse_control as mc  # noqa: E402
from screen_capture import Monitor  # noqa: E402

kc.wi.send = win_input.send
mc.wi.send = win_input.send
ok = 0
fails = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
        print("[PASSOU]", name)
    else:
        fails.append(name)
        print("[FALHOU]", name)


# --- mouse: monitor principal 1920x1080 em (0,0), desktop virtual 3840x1082 em (0,-2)
m = mc.MouseController(Monitor(1, 0, 0, 1920, 1080, True), Monitor(0, 0, -2, 3840, 1082))
sent.clear()
m.move(0.5, 0.5)
ev = sent[0].mi
check("movimento normalizado -> absoluto no desktop virtual",
      abs(ev.dx - round(959.5 * 65535 / 3839)) <= 1 and abs(ev.dy - round(541.5 * 65535 / 1081)) <= 1)
check("flags ABSOLUTE|VIRTUALDESK|MOVE", ev.dwFlags == (0x1 | 0x8000 | 0x4000))
sent.clear()
m.move(5, -3)  # fora do intervalo é limitado
check("coordenadas fora de 0..1 são limitadas", sent[0].mi.dx == round(1919 * 65535 / 3839) and sent[0].mi.dy == round(2 * 65535 / 1081))
for bad in (float("nan"), float("inf")):
    try:
        m.move(bad, 0.5)
        check(f"coordenada {bad} rejeitada", False)
    except ValueError:
        check(f"coordenada {bad} rejeitada", True)
sent.clear()
m.button(2, True)
m.button(2, False)
check("botão direito down/up", [e.mi.dwFlags for e in sent] == [0x8, 0x10])
m.button(0, True)
sent.clear()
m.release_all()
check("release_all solta o botão esquerdo preso", [e.mi.dwFlags for e in sent] == [0x4])
try:
    m.button(9, True)
    check("botão inválido rejeitado", False)
except ValueError:
    check("botão inválido rejeitado", True)

# --- teclado
k = kc.KeyboardController()
sent.clear()
k.key("KeyA", "á", True)
k.key("KeyA", "á", False)
check("caractere acentuado via UNICODE", sent[0].ki.dwFlags == 0x4 and sent[0].ki.wScan == ord("á") and sent[1].ki.dwFlags == 0x6)
sent.clear()
k.key("BracketLeft", "Dead", True)
check("tecla morta não é enviada", not sent)
sent.clear()
k.key("Backspace", "Backspace", True)
check("Backspace (nome longo) enviado como VK_BACK", sent and sent[0].ki.wVk == 0x08)
k.key("Backspace", "Backspace", False)
sent.clear()
k.key("ControlLeft", "Control", True)
k.key("KeyC", "c", True)
check("Ctrl+C envia VK 'C' (não unicode) com Ctrl pressionado", sent[1].ki.wVk == ord("C") and not sent[1].ki.dwFlags & 0x4)
sent.clear()
k.release_all()
check("release_all solta Ctrl e C", sorted(e.ki.wVk for e in sent) == sorted([0xA2, ord("C")]) and all(e.ki.dwFlags & 0x2 for e in sent))
sent.clear()
k.key("ArrowLeft", "ArrowLeft", True)
check("seta é tecla estendida", sent[0].ki.dwFlags & 0x1 == 0x1)
k.release_all()
sent.clear()
k.shortcut("ctrl-shift-esc")
check("atalho Ctrl+Shift+Esc: 3 down + 3 up em ordem", [e.ki.wVk for e in sent] == [0xA2, 0xA0, 0x1B, 0x1B, 0xA0, 0xA2])
for bad in (("Evil", "ScriptRun"), ("KeyA" * 20, "a")):
    try:
        k.key(bad[0], bad[1], True)
        check(f"tecla desconhecida/inválida rejeitada {bad[0][:10]}", False)
    except ValueError:
        check(f"tecla desconhecida/inválida rejeitada {bad[0][:10]}", True)
try:
    k.shortcut("cmd /c calc")
    check("atalho inexistente rejeitado", False)
except ValueError:
    check("atalho inexistente rejeitado", True)

print(f"\nRESULTADO: {ok}/{ok + len(fails)} aprovados")
sys.exit(1 if fails else 0)
