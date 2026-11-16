"""Estruturas e chamada SendInput (user32) compartilhadas por mouse_control e keyboard_control."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

ULONG_PTR = ctypes.c_size_t
HP2P_EXTRA_INFO = 0x48503250  # marca os eventos injetados pelo HP2P ("HP2P")

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_user32.MapVirtualKeyW.restype = wintypes.UINT


def send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    n = _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n != len(inputs):
        # Falha típica: janela em primeiro plano é de processo elevado (UIPI) ou desktop seguro.
        raise OSError(ctypes.get_last_error(), "SendInput bloqueado (UIPI/desktop seguro?)")
    return n


def mouse(dx: int = 0, dy: int = 0, data: int = 0, flags: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, HP2P_EXTRA_INFO)
    return i


def key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, HP2P_EXTRA_INFO)
    return i


def vk_to_scan(vk: int) -> int:
    return _user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
