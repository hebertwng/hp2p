"""Controle de teclado via SendInput.

Estratégia:
  * Caracteres imprimíveis sem Ctrl/Alt/Win pressionados -> KEYEVENTF_UNICODE com o caractere
    (event.key). Funciona mesmo que o layout do teclado do operador seja diferente do remoto.
  * Demais teclas (Ctrl, Alt, Shift, Win, Enter, Esc, Backspace, Delete, F1-F24, setas, atalhos)
    -> tecla virtual (VK) + scan code mapeados a partir de KeyboardEvent.code.
  * Teclas "mortas" (acentos) não são enviadas: o navegador entrega o caractere já composto.
  * Todas as teclas pressionadas são liberadas ao perder a conexão (release_all).

Não existe nenhum caminho para executar comandos/scripts: só eventos de tecla de uma lista fixa.
Ctrl+Alt+Del (SAS) não pode ser injetado por SendInput por desenho do Windows.
"""

from __future__ import annotations

import logging

import win_input as wi

log = logging.getLogger("hp2p.keyboard")

EXTENDED, KEYUP, UNICODE = 0x1, 0x2, 0x4

# KeyboardEvent.code -> (VK, estendida?)
CODE_TO_VK: dict[str, tuple[int, bool]] = {
    **{f"Key{chr(c)}": (c, False) for c in range(ord("A"), ord("Z") + 1)},
    **{f"Digit{d}": (0x30 + d, False) for d in range(10)},
    **{f"F{n}": (0x6F + n, False) for n in range(1, 25)},
    **{f"Numpad{d}": (0x60 + d, False) for d in range(10)},
    "NumpadMultiply": (0x6A, False), "NumpadAdd": (0x6B, False), "NumpadSubtract": (0x6D, False),
    "NumpadDecimal": (0x6E, False), "NumpadDivide": (0x6F, True), "NumpadEnter": (0x0D, True),
    "NumpadComma": (0xC2, False),
    "Escape": (0x1B, False), "Tab": (0x09, False), "CapsLock": (0x14, False), "Space": (0x20, False),
    "Enter": (0x0D, False), "Backspace": (0x08, False),
    "ShiftLeft": (0xA0, False), "ShiftRight": (0xA1, False),
    "ControlLeft": (0xA2, False), "ControlRight": (0xA3, True),
    "AltLeft": (0xA4, False), "AltRight": (0xA5, True),
    "MetaLeft": (0x5B, True), "MetaRight": (0x5C, True), "OSLeft": (0x5B, True), "OSRight": (0x5C, True),
    "ContextMenu": (0x5D, True),
    "Insert": (0x2D, True), "Delete": (0x2E, True), "Home": (0x24, True), "End": (0x23, True),
    "PageUp": (0x21, True), "PageDown": (0x22, True),
    "ArrowLeft": (0x25, True), "ArrowUp": (0x26, True), "ArrowRight": (0x27, True), "ArrowDown": (0x28, True),
    "PrintScreen": (0x2C, True), "ScrollLock": (0x91, False), "Pause": (0x13, False), "NumLock": (0x90, True),
    "Minus": (0xBD, False), "Equal": (0xBB, False), "BracketLeft": (0xDB, False), "BracketRight": (0xDD, False),
    "Backslash": (0xDC, False), "Semicolon": (0xBA, False), "Quote": (0xDE, False), "Backquote": (0xC0, False),
    "Comma": (0xBC, False), "Period": (0xBE, False), "Slash": (0xBF, False),
    "IntlBackslash": (0xE2, False), "IntlRo": (0xC1, False),
}
MODIFIER_CODES = {"ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight",
                  "OSLeft", "OSRight"}

# Atalhos oferecidos como botões no controlador (o navegador não captura a tecla Windows).
SHORTCUTS: dict[str, list[str]] = {
    "win": ["MetaLeft"],
    "alt-tab": ["AltLeft", "Tab"],
    "ctrl-shift-esc": ["ControlLeft", "ShiftLeft", "Escape"],
    "win-d": ["MetaLeft", "KeyD"],
    "win-e": ["MetaLeft", "KeyE"],
    "win-r": ["MetaLeft", "KeyR"],
    "win-l": ["MetaLeft", "KeyL"],
    "alt-f4": ["AltLeft", "F4"],
    "printscreen": ["PrintScreen"],
}


class KeyboardController:
    def __init__(self) -> None:
        # code -> ("vk", vk, ext) | ("uni", texto)
        self.pressed: dict[str, tuple] = {}

    def _mods_active(self) -> bool:
        return any(c in MODIFIER_CODES for c in self.pressed)

    @staticmethod
    def _vk_event(vk: int, ext: bool, up: bool) -> wi.INPUT:
        flags = (EXTENDED if ext else 0) | (KEYUP if up else 0)
        return wi.key(vk, wi.vk_to_scan(vk), flags)

    @staticmethod
    def _uni_events(text: str, up: bool) -> list[wi.INPUT]:
        units = text.encode("utf-16-le")
        return [wi.key(0, int.from_bytes(units[i:i + 2], "little"), UNICODE | (KEYUP if up else 0))
                for i in range(0, len(units), 2)]

    def key(self, code: str, key: str, down: bool) -> None:
        # KeyboardEvent.key pode ser um nome longo ("Backspace", "ArrowRight", "AudioVolumeMute")
        if not isinstance(code, str) or not isinstance(key, str) or len(code) > 32 or len(key) > 32:
            raise ValueError("tecla inválida")
        if key == "Dead":
            return
        if not down:
            entry = self.pressed.pop(code, None)
            if entry is None:
                return
            if entry[0] == "vk":
                wi.send([self._vk_event(entry[1], entry[2], True)])
            else:
                wi.send(self._uni_events(entry[1], True))
            return
        printable = len(key) <= 2 and key.isprintable() and key != " " and len(key.encode("utf-16-le")) <= 4
        if printable and not self._mods_active():
            wi.send(self._uni_events(key, False))
            self.pressed[code] = ("uni", key)
            return
        if code not in CODE_TO_VK:
            raise ValueError(f"tecla não suportada: {code}")
        vk, ext = CODE_TO_VK[code]
        wi.send([self._vk_event(vk, ext, False)])
        self.pressed[code] = ("vk", vk, ext)

    def shortcut(self, name: str) -> None:
        codes = SHORTCUTS.get(name)
        if not codes:
            raise ValueError("atalho inválido")
        downs = [self._vk_event(*CODE_TO_VK[c], False) for c in codes]
        ups = [self._vk_event(*CODE_TO_VK[c], True) for c in reversed(codes)]
        wi.send(downs + ups)

    def release_all(self) -> None:
        for code in list(self.pressed):
            try:
                self.key(code, "", False)
            except OSError:
                pass
        self.pressed.clear()
