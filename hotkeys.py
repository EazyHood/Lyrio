# -*- coding: utf-8 -*-
"""Atajos de teclado globales (funcionan aunque la app no tenga el foco).

Ctrl+Alt+L  -> mostrar/ocultar letra flotante
Ctrl+Alt+→  -> letra 0.25 s antes     Ctrl+Alt+←  -> letra 0.25 s despues
Ctrl+Alt+↑  -> texto mas grande       Ctrl+Alt+↓  -> texto mas chico
"""
import ctypes
import threading
from ctypes import wintypes

MOD = 0x0002 | 0x0001          # MOD_CONTROL | MOD_ALT
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_L, VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x4C, 0x25, 0x26, 0x27, 0x28

ACTIONS = {
    1: ("toggle_overlay", VK_L),
    2: ("offset_plus", VK_RIGHT),
    3: ("offset_minus", VK_LEFT),
    4: ("font_bigger", VK_UP),
    5: ("font_smaller", VK_DOWN),
}


class GlobalHotkeys:
    """dispatch(action_name) se llama YA marshallado al hilo de la UI."""

    def __init__(self, dispatch):
        self._dispatch = dispatch
        self._thread = None
        self._tid = None
        self._enabled = False

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="hotkeys")
        self._thread.start()

    def stop(self):
        if self._tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT,
                                                        0, 0)
            except Exception:
                pass
        self._thread = None

    def _run(self):
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        self._tid = k.GetCurrentThreadId()
        registered = []
        for hid, (_name, vk) in ACTIONS.items():
            if u.RegisterHotKey(None, hid, MOD, vk):
                registered.append(hid)
        self._enabled = bool(registered)
        try:
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    action = ACTIONS.get(msg.wParam)
                    if action:
                        try:
                            self._dispatch(action[0])
                        except Exception:
                            pass
        finally:
            for hid in registered:
                try:
                    u.UnregisterHotKey(None, hid)
                except Exception:
                    pass
