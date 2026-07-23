# -*- coding: utf-8 -*-
"""Configuracion central de Lyrio + utilidades de sistema.

Config en %APPDATA%/Lyrio/config.json (escribible tambien como .exe).
Un solo dict compartido; solo el hilo de la UI lo escribe.
"""
import ctypes
import json
import os
import sys
import threading

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", "."), "Lyrio")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

APP_NAME = "Lyrio"
APP_VERSION = "2.0.0"

MUTEX_NAME = "Local\\Lyrio_SingleInstance"
SHOW_EVENT_NAME = "Local\\Lyrio_ShowWindow"

DEFAULTS = {
    "language": "en",             # en | es
    "appearance": "system",       # light | dark | system
    "autostart": False,
    "offset_global": 0.0,
    "tray_notice_shown": False,
    "overlay_hint_shown": False,
    "source_mode": "spotify",     # spotify | any (cualquier reproductor SMTC)
    "hotkeys": True,              # atajos globales Ctrl+Alt+...
    "sync_author": "",            # firma [by:] al publicar sincronizaciones
    "overlay": {
        "x": None, "y": None,
        "theme": "classic",       # classic | cartoon | neon | minimal
        "sweep_mode": "word",     # word (palabra completa) | char (letra)
        "outline_px": 2,
        "caps": False,
        "font_family": "Segoe UI",
        "font_size": 30,          # tamano logico (96 DPI)
        "color": "#ffffff",
        "show_next": True,        # compat v2 (migra a lines_mode)
        "lines_mode": None,       # 1 = actual, 2 = +siguiente, 3 = +anterior
        "width_pct": 72,          # % del ancho de pantalla (30-95)
        "align": "center",        # left | center | right
        "bg_card": False,         # tarjeta oscura detras del texto
        "progress_line": True,    # subrayado de progreso de la linea
        "ghost": False,
        "visible": True,
    },
    "overlay2": {
        "x": None, "y": None,
        "font_family": "Segoe UI",
        "font_size": 24,
        "color": "#4dd6ff",
        "show_next": True,
        "lines_mode": 2,
        "width_pct": 60,
        "align": "center",
        "bg_card": False,
        "progress_line": False,
        "ghost": False,
        "visible": True,
    },
}


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _merge(base[k], v)
        elif k in base:
            out[k] = v
    return out


def _system_language():
    """'es' si la UI de Windows esta en espanol; 'en' en caso contrario."""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "es" if (langid & 0xFF) == 0x0A else "en"
    except Exception:
        return "en"


def _migrate_from_letraviva():
    """Migra config/cache del nombre viejo (LetraViva) si existen."""
    import shutil
    old_cfg = os.path.join(os.environ.get("APPDATA", "."), "LetraViva")
    if os.path.isdir(old_cfg) and not os.path.exists(CONFIG_PATH):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            src = os.path.join(old_cfg, "config.json")
            if os.path.exists(src):
                shutil.copy2(src, CONFIG_PATH)
        except OSError:
            pass
    old_data = os.path.join(os.environ.get("LOCALAPPDATA", "."), "LetraViva")
    new_data = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Lyrio")
    if os.path.isdir(old_data) and not os.path.isdir(new_data):
        try:
            shutil.copytree(old_data, new_data)
        except OSError:
            pass


_migrate_from_letraviva()


class Config:
    def __init__(self):
        raw = {}
        fresh = True
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            fresh = False
        except Exception:
            pass
        self.data = _merge(DEFAULTS, raw if isinstance(raw, dict) else {})
        if fresh:
            # primer arranque: heredar el idioma de Windows
            self.data["language"] = _system_language()
        # migracion v2 -> v4: lines_mode desde show_next
        ov = self.data["overlay"]
        if ov.get("lines_mode") not in (1, 2, 3):
            ov["lines_mode"] = 2 if ov.get("show_next", True) else 1

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except OSError:
            pass

    @property
    def overlay(self):
        return self.data["overlay"]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()


def exe_path():
    """Ruta del ejecutable real (exe congelado o script)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def set_autostart(enabled):
    """Alta/baja en el arranque de Windows (HKCU Run). Devuelve True si se pudo."""
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enabled:
                if getattr(sys, "frozen", False):
                    cmd = f'"{exe_path()}" --hidden'
                else:
                    pyw = sys.executable.replace("python.exe", "pythonw.exe")
                    if not os.path.exists(pyw):
                        pyw = sys.executable
                    cmd = f'"{pyw}" "{exe_path()}" --hidden'
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ------------------------------------------------- instancia unica

_mutex_handle = None
ERROR_ALREADY_EXISTS = 183

# WinDLL propio con use_last_error: ctypes.windll.GetLastError() no es fiable
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = ctypes.c_void_p
_k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
_k32.OpenEventW.restype = ctypes.c_void_p
_k32.OpenEventW.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
_k32.CreateEventW.restype = ctypes.c_void_p
_k32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_wchar_p)
_k32.SetEvent.argtypes = (ctypes.c_void_p,)
_k32.CloseHandle.argtypes = (ctypes.c_void_p,)
_k32.WaitForSingleObject.restype = ctypes.c_uint32
_k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)


def acquire_single_instance():
    """True si somos la primera instancia. Si ya hay otra, le pide que se
    muestre (evento nombrado) y devuelve False."""
    global _mutex_handle
    ctypes.set_last_error(0)
    _mutex_handle = _k32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        ev = _k32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)  # EVENT_MODIFY_STATE
        if ev:
            _k32.SetEvent(ev)
            _k32.CloseHandle(ev)
        return False
    return True


def watch_show_event(callback, stop_event):
    """Hilo que espera el evento 'muestrate' de otras instancias."""
    def run():
        ev = _k32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        if not ev:
            return
        try:
            while not stop_event.is_set():
                # WAIT_OBJECT_0 = 0, timeout 500 ms
                if _k32.WaitForSingleObject(ev, 500) == 0:
                    try:
                        callback()
                    except Exception:
                        pass
        finally:
            _k32.CloseHandle(ev)

    t = threading.Thread(target=run, daemon=True, name="show-event")
    t.start()
    return t
