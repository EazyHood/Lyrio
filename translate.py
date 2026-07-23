# -*- coding: utf-8 -*-
"""Traduccion en vivo (Ollama local) y romanizacion de letras.

- Traduccion: linea a linea contra el Ollama local (qwen2.5:3b u otro),
  con cache en memoria por cancion y peticiones en un hilo unico. Si Ollama
  no responde, la funcion queda muda (sin errores para el usuario).
- Romanizacion: japones (pykakasi), chino (pypinyin), coreano
  (korean_romanizer), 100% offline. Se activa sola si la linea es CJK.
"""
import json
import re
import threading

import requests

OLLAMA = "http://localhost:11434"
MODEL = "qwen2.5:3b"

_JA = re.compile(r"[぀-ヿ]")            # kana
_ZH = re.compile(r"[一-鿿]")            # han
_KO = re.compile(r"[가-힯]")            # hangul
_LATIN = re.compile(r"[a-zA-ZÀ-ɏ]")

_kakasi = None
_LANG_NAMES = {"es": "Spanish", "en": "English"}


def needs_romanization(text):
    return bool(_JA.search(text) or _ZH.search(text) or _KO.search(text))


def romanize(text):
    """Romaniza una linea CJK; devuelve "" si no aplica o falta el paquete."""
    global _kakasi
    try:
        if _JA.search(text):
            import pykakasi
            if _kakasi is None:
                _kakasi = pykakasi.kakasi()
            out = " ".join(item["hepburn"] for item in _kakasi.convert(text))
            return out.strip()
        if _ZH.search(text):
            from pypinyin import lazy_pinyin
            return " ".join(lazy_pinyin(text)).strip()
        if _KO.search(text):
            from korean_romanizer.romanizer import Romanizer
            return Romanizer(text).romanize().strip()
    except Exception:
        pass
    return ""


class Translator:
    """Traduce lineas en segundo plano con cache. on_update() avisa a la UI."""

    def __init__(self, on_update=None):
        self._cache = {}          # texto -> traduccion
        self._pending = []
        self._lock = threading.Lock()
        self._on_update = on_update
        self._alive = None        # None = sin probar, True/False tras probar
        self._wake = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._worker, daemon=True,
                         name="translator").start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def reset(self):
        with self._lock:
            self._cache.clear()
            self._pending.clear()

    def get(self, text, target_lang):
        """Traduccion cacheada de la linea, o "" si aun no esta (la encola)."""
        if not text or len(text) < 2:
            return ""
        if not _LATIN.search(text) and not needs_romanization(text):
            return ""
        key = (text, target_lang)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if key not in self._pending:
                self._pending.append(key)
                if len(self._pending) > 30:
                    self._pending.pop(0)
        self._wake.set()
        return ""

    # ------------------------------------------------------------------

    def _check_alive(self):
        if self._alive is not None:
            return self._alive
        try:
            requests.get(OLLAMA + "/api/tags", timeout=2)
            self._alive = True
        except requests.RequestException:
            self._alive = False
        return self._alive

    def _worker(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            while True:
                with self._lock:
                    if not self._pending:
                        break
                    key = self._pending.pop(0)
                if key in self._cache:
                    continue
                if not self._check_alive():
                    with self._lock:
                        self._pending.clear()
                    break
                text, lang = key
                out = self._translate(text, lang)
                with self._lock:
                    self._cache[key] = out
                if out and self._on_update:
                    try:
                        self._on_update()
                    except Exception:
                        pass

    def _translate(self, text, target_lang):
        name = _LANG_NAMES.get(target_lang, "Spanish")
        try:
            r = requests.post(
                OLLAMA + "/api/generate",
                json={"model": MODEL, "stream": False,
                      "options": {"temperature": 0.1, "num_predict": 60},
                      "prompt": (f"Translate this song lyric line to {name}. "
                                 "Reply ONLY with the translation, nothing "
                                 f"else.\nLine: {text}\nTranslation:")},
                timeout=20)
            out = (r.json().get("response") or "").strip()
            out = out.strip('"').splitlines()[0].strip() if out else ""
            # si el modelo devolvio lo mismo o basura larga, descartar
            if out.lower() == text.lower() or len(out) > 3 * len(text) + 20:
                return ""
            return out
        except Exception:
            self._alive = None      # reprobar mas tarde
            return ""
